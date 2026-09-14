from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..level6.contracts import Level6Result
from .contracts import EvolutionExperiment, EvolutionHypothesis, EvolutionMetrics, Lesson
from .optimizer import ModelPromptOptimizer


@dataclass(frozen=True)
class BenchmarkSuite:
    name: str
    commands: tuple[tuple[str, ...], ...]
    coverage_file: str | None = None
    timeout_seconds: int = 1200

    @classmethod
    def from_json(cls, path: Path) -> BenchmarkSuite:
        data = json.loads(path.read_text("utf-8"))
        commands = tuple(tuple(str(part) for part in command) for command in data["commands"])
        return cls(
            name=str(data["name"]),
            commands=commands,
            coverage_file=data.get("coverage_file"),
            timeout_seconds=int(data.get("timeout_seconds", 1200)),
        )


class GitWorktreeBenchmarkProvider:
    """Measures any Git ref in a detached, disposable worktree."""

    def __init__(self, repository: Path, suites: dict[str, BenchmarkSuite]) -> None:
        self.repository = repository.resolve()
        self.suites = suites

    def measure(self, ref: str, benchmark: str) -> EvolutionMetrics:
        if benchmark not in self.suites:
            raise KeyError(f"unknown benchmark: {benchmark}")
        suite = self.suites[benchmark]
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="rea-benchmark-") as temporary:
            root = Path(temporary)
            self._git("worktree", "add", "--detach", str(root), ref)
            try:
                passed = 0
                for command in suite.commands:
                    result = subprocess.run(
                        command,
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=suite.timeout_seconds,
                        check=False,
                        env={\n                            **os.environ,\n                            "REA_BENCHMARK": "1",\n                            "PYTHONPATH": str(root / "src"),\n                        },
                    )
                    if result.returncode == 0:
                        passed += 1
                coverage = self._coverage(root / suite.coverage_file) if suite.coverage_file else 0
            finally:
                self._git("worktree", "remove", "--force", str(root))
        total = max(len(suite.commands), 1)
        rate = passed / total
        return EvolutionMetrics(
            task_success_rate=rate,
            first_pass_rate=rate,
            coverage=coverage,
            latency_seconds=time.monotonic() - started,
        )

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=self.repository,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )

    @staticmethod
    def _coverage(path: Path) -> float:
        if not path.exists():
            return 0
        data = json.loads(path.read_text("utf-8"))
        percent = data.get("totals", {}).get("percent_covered", 0)
        return min(1.0, max(0.0, float(percent) / 100))


class Level6CandidateBuilder:
    """Connects a hypothesis to the existing governed Issue-to-PR engine."""

    def __init__(
        self,
        *,
        issue_creator: Callable[[EvolutionHypothesis, list[Lesson]], int],
        issue_runner: Callable[[int], Level6Result],
        changed_paths: Callable[[str, str], list[str]],
        lessons: Callable[[str, str], list[Lesson]],
    ) -> None:
        self.issue_creator = issue_creator
        self.issue_runner = issue_runner
        self.changed_paths = changed_paths
        self.lessons = lessons
        self.last_pull_request_url: str | None = None

    def build(
        self, hypothesis: EvolutionHypothesis, baseline_ref: str
    ) -> tuple[str, list[str]]:
        relevant = self.lessons(hypothesis.repository, hypothesis.id)
        issue_number = self.issue_creator(hypothesis, relevant)
        result = self.issue_runner(issue_number)
        if result.status != "draft_pr_created":
            raise RuntimeError(f"Level 6 did not create a candidate: {result.status}")
        self.last_pull_request_url = result.pull_request_url
        return result.branch, self.changed_paths(baseline_ref, result.branch)


class Level6PullRequestPublisher:
    """Level 6 already opens the PR; this adapter verifies and returns it."""

    def __init__(self, builder: Level6CandidateBuilder) -> None:
        self.builder = builder

    def publish(self, experiment: EvolutionExperiment) -> str:
        url = self.builder.last_pull_request_url
        if not url:
            raise RuntimeError("candidate has no Level 6 pull request")
        return url


class AdaptiveCandidateBuilder:
    """Selects a benchmark-rewarded model and scopes the override to one Level 6 run."""

    def __init__(
        self,
        builder: Level6CandidateBuilder,
        optimizer: ModelPromptOptimizer,
    ) -> None:
        self.builder = builder
        self.optimizer = optimizer
        self.selected_name: str | None = None

    @property
    def last_pull_request_url(self) -> str | None:
        return self.builder.last_pull_request_url

    def build(
        self, hypothesis: EvolutionHypothesis, baseline_ref: str
    ) -> tuple[str, list[str]]:
        selected = self.optimizer.choose()
        self.selected_name = selected
        previous = os.environ.get("REA_EVOLUTION_MODEL")
        os.environ["REA_EVOLUTION_MODEL"] = selected
        try:
            return self.builder.build(hypothesis, baseline_ref)
        finally:
            if previous is None:
                os.environ.pop("REA_EVOLUTION_MODEL", None)
            else:
                os.environ["REA_EVOLUTION_MODEL"] = previous

    def record_reward(self, delta: float, passed: bool) -> None:
        if self.selected_name is None:
            return
        reward = max(-1.0, min(1.0, delta / 10))
        if not passed:
            reward = min(reward, -0.1)
        self.optimizer.record(self.selected_name, reward)
