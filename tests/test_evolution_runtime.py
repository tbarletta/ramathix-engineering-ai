import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rea.audit import AuditLog
from rea.domain import Decision
from rea.evolution import (
    BenchmarkSuite,
    CommandDeploymentAdapter,
    DeploymentCommands,
    EvolutionBudget,
    EvolutionHypothesis,
    EvolutionState,
    EvolutionWorker,
    Level6CandidateBuilder,
    RepositoryLock,
    StateStore,
)
from rea.evolution.contracts import EvolutionMetrics
from rea.execution import GovernedLocalRunner
from rea.policy import CommandPolicy


def hypothesis():
    return EvolutionHypothesis(
        id="hyp-failure",
        repository="owner/repo",
        problem="failure",
        hypothesis="fix it",
        evidence=["one", "two"],
        baseline=0.5,
        target=0.9,
        risk="low",
    )


def test_benchmark_suite_loads_versioned_commands(tmp_path: Path):
    path = tmp_path / "suite.json"
    path.write_text(
        json.dumps({"name": "golden", "commands": [["python", "-m", "pytest"]]}),
        encoding="utf-8",
    )
    suite = BenchmarkSuite.from_json(path)
    assert suite.name == "golden"
    assert suite.commands[0][-1] == "pytest"


def test_state_survives_restart(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    state = EvolutionState(consecutive_failures=2, active_experiment="hyp-1")
    store.save(state)
    assert store.load() == state


def test_repository_lock_rejects_concurrent_owner(tmp_path: Path):
    path = tmp_path / "worker.lock"
    with RepositoryLock(path):
        with pytest.raises(RuntimeError, match="another evolution worker"):
            with RepositoryLock(path):
                pass
    assert not path.exists()


def test_level6_builder_injects_durable_lessons(tmp_path: Path):
    seen = {}

    def create_issue(item, lessons):
        seen["lessons"] = lessons
        return 7

    result = SimpleNamespace(
        status="draft_pr_created",
        branch="rea/issue-7",
        pull_request_url="https://github.com/owner/repo/pull/7",
    )
    builder = Level6CandidateBuilder(
        issue_creator=create_issue,
        issue_runner=lambda number: result,
        changed_paths=lambda base, head: ["src/fix.py"],
        lessons=lambda repository, fingerprint: ["prior lesson"],
    )
    ref, paths = builder.build(hypothesis(), "main")
    assert ref == "rea/issue-7"
    assert paths == ["src/fix.py"]
    assert seen["lessons"] == ["prior lesson"]


def test_command_deployment_verifies_rollback(tmp_path: Path):
    commands = DeploymentCommands(
        shadow=("python", "-c", "print('shadow-1')"),
        canary=("python", "-c", "print('canary-1')"),
        health=("python", "-c", "raise SystemExit(0)"),
        promote=("python", "-c", "print('ok')"),
        rollback=("python", "-c", "print('rolled')"),
        verify_rollback=("python", "-c", "raise SystemExit(0)"),
    )
    policy = CommandPolicy(default=Decision.ALLOW, rules=[])
    adapter = CommandDeploymentAdapter(
        commands,
        tmp_path,
        runner=GovernedLocalRunner(policy, AuditLog(tmp_path / "audit.jsonl")),
    )
    assert adapter.deploy_shadow("candidate") == "shadow-1"
    adapter.rollback("shadow-1")


class FakeWorkflow:
    def __init__(self):
        self.consecutive_failures = 0
        self.calls = 0

    def run(self, item, **kwargs):
        self.calls += 1
        evaluation = SimpleNamespace(candidate=EvolutionMetrics(gpu_minutes=1))
        experiment = SimpleNamespace(
            id="exp-1",
            evaluation=evaluation,
            pull_request_url="https://github.com/owner/repo/pull/1",
        )
        return experiment, SimpleNamespace(allowed=True)


def test_worker_is_idempotent_and_persists_budget(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    records = [
        {
            "id": f"run-{number}",
            "event": "ci.failed",
            "timestamp": "2026-09-14T00:00:00Z",
            "data": {"summary": "same failure", "fingerprint": "stable"},
        }
        for number in range(2)
    ]
    audit.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    workflow = FakeWorkflow()
    state_store = StateStore(tmp_path / "state.json")
    worker = EvolutionWorker(
        repository="owner/repo",
        audit_path=audit,
        workflow=workflow,
        state_store=state_store,
        lock_path=tmp_path / "worker.lock",
    )
    budget = EvolutionBudget(max_experiments=2, max_gpu_minutes=10)
    assert worker.tick(baseline_ref="main", benchmark="golden", budget=budget) == 1
    assert worker.tick(baseline_ref="main", benchmark="golden", budget=budget) == 0
    assert workflow.calls == 1
    assert state_store.load().gpu_minutes_today == 1
