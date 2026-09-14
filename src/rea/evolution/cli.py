from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..audit import AuditLog
from ..cli import _run_level6_issue
from ..config import Settings
from ..level6.contracts import Level6Result
from .contracts import EvolutionHypothesis
from .discovery import AuditObserver, OpportunityDetector
from .deployment import CommandDeploymentAdapter, DeploymentCommands
from .evaluation import EvaluationEngine
from .github import GitHubEvolutionClient
from .optimizer import ModelPromptOptimizer
from .promotion import CanaryController, PromotionPolicy
from .runtime import (
    AdaptiveCandidateBuilder,
    BenchmarkSuite,
    GitWorktreeBenchmarkProvider,
    Level6CandidateBuilder,
    Level6PullRequestPublisher,
)
from .state import StateStore
from .store import JsonEvolutionStore
from .worker import EvolutionWorker
from .workflow import EvolutionBudget, EvolutionWorkflow

app = typer.Typer(help="REA autonomous evolution control plane.")


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _hypothesis(path: Path) -> EvolutionHypothesis:
    return EvolutionHypothesis(**json.loads(path.read_text("utf-8")))


def _components(
    *,
    repository: str,
    workspace: Path,
    suite_path: Path,
    approve_rules: set[str],
    allow_network: bool,
) -> tuple[EvolutionWorkflow, str]:
    if "github-issue-create" not in approve_rules:
        raise PermissionError("--approve-rule github-issue-create is required")
    settings = Settings.from_env()
    store = JsonEvolutionStore(settings.home / ".rea" / "evolution")
    github = GitHubEvolutionClient()
    suite = BenchmarkSuite.from_json(suite_path)
    provider = GitWorktreeBenchmarkProvider(workspace, {suite.name: suite})

    def run_issue(number: int) -> Level6Result:
        return _run_level6_issue(
            settings,
            number=number,
            repository=repository,
            workspace=workspace,
            base="main",
            knowledge_repo=None,
            image=None,
            approved_rules=approve_rules,
            allow_network=allow_network,
            max_iterations=3,
        )

    builder = Level6CandidateBuilder(
        issue_creator=github.create_hypothesis_issue,
        issue_runner=run_issue,
        changed_paths=lambda base, head: github.changed_paths(repository, base, head),
        lessons=store.lessons,
    )
    candidates = [
        item.strip()
        for item in os.getenv("REA_EVOLUTION_MODELS", "").split(",")
        if item.strip()
    ]
    candidate_builder = builder
    if candidates:
        candidate_builder = AdaptiveCandidateBuilder(
            builder,
            ModelPromptOptimizer(
                settings.home / ".rea" / "evolution" / "optimizer.json",
                candidates,
            ),
        )
    workflow = EvolutionWorkflow(
        evaluator=EvaluationEngine(provider),
        builder=candidate_builder,
        publisher=Level6PullRequestPublisher(candidate_builder),
        store=store,
        audit=AuditLog(settings.audit_path),
    )
    return workflow, suite.name


def _promote_if_ready(
    repository: str,
    experiment_id: str,
    pull_request: int,
    store: JsonEvolutionStore,
) -> bool:
    experiment = store.load_experiment(experiment_id)
    if not experiment.evaluation or not experiment.candidate_ref:
        return True
    github = GitHubEvolutionClient()
    paths = github.changed_paths(repository, experiment.baseline_ref, experiment.candidate_ref)
    decision = PromotionPolicy().decide(
        experiment.evaluation,
        risk=experiment.hypothesis.risk,
        changed_paths=paths,
        cost_impact=False,
        approvals={"tech-lead"},
    )
    if not decision.allowed:
        return True
    if not github.branch_protected(repository):
        return False
    state = github.pull_request_state(repository, pull_request)
    if state["merged"] or state["state"] == "closed":
        return True
    if state["mergeable"] is not True or not github.checks_green(repository, state["sha"]):
        return False
    if state["draft"]:
        github.mark_ready(state["node_id"])
    return github.merge(repository, pull_request, state["sha"])


@app.command("discover")
def discover(
    repository: str = typer.Option(..., "--repo"),
    audit_path: Path = typer.Option(Path(".rea/audit.jsonl"), "--audit"),
    minimum_occurrences: int = typer.Option(2, "--minimum-occurrences", min=1),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Discover evidence-backed opportunities without changing a repository."""
    events = AuditObserver().observe(_records(audit_path), repository)
    hypotheses = OpportunityDetector(minimum_occurrences).detect(events)
    payload = [item.__dict__ for item in hypotheses]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    typer.echo(rendered)


@app.command("run")
def run(
    hypothesis: Path,
    repository: str = typer.Option(..., "--repo"),
    workspace: Path = typer.Option(Path("."), "--workspace"),
    suite: Path = typer.Option(Path("config/evolution-benchmark.json"), "--suite"),
    baseline: str = typer.Option("main", "--baseline"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
    allow_network: bool = typer.Option(False, "--allow-network"),
    max_gpu_minutes: float = typer.Option(30, "--max-gpu-minutes"),
) -> None:
    """Run one governed hypothesis through Level 6, benchmark and PR publication."""
    workflow, benchmark = _components(
        repository=repository,
        workspace=workspace.resolve(),
        suite_path=suite,
        approve_rules=set(approve_rule),
        allow_network=allow_network,
    )
    experiment, decision = workflow.run(
        _hypothesis(hypothesis),
        baseline_ref=baseline,
        benchmark=benchmark,
        budget=EvolutionBudget(max_gpu_minutes=max_gpu_minutes),
    )
    typer.echo(
        json.dumps(
            {"experiment": experiment.to_dict(), "promotion": decision.__dict__},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("daemon")
def daemon(
    repository: str = typer.Option(..., "--repo"),
    workspace: Path = typer.Option(Path("."), "--workspace"),
    suite: Path = typer.Option(Path("config/evolution-benchmark.json"), "--suite"),
    baseline: str = typer.Option("main", "--baseline"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
    interval: int = typer.Option(3600, "--interval", min=60),
    max_experiments: int = typer.Option(1, "--max-experiments", min=1),
    max_gpu_minutes: float = typer.Option(30, "--max-gpu-minutes"),
    once: bool = typer.Option(False, "--once"),
) -> None:
    """Continuously discover and execute bounded, idempotent evolution experiments."""
    settings = Settings.from_env()
    workflow, benchmark = _components(
        repository=repository,
        workspace=workspace.resolve(),
        suite_path=suite,
        approve_rules=set(approve_rule),
        allow_network=False,
    )
    root = settings.home / ".rea" / "evolution"
    worker = EvolutionWorker(
        repository=repository,
        audit_path=settings.audit_path,
        workflow=workflow,
        state_store=StateStore(root / "state.json"),
        lock_path=root / "worker.lock",
        event_source=lambda: GitHubEvolutionClient().evolution_records(repository),
        promotion_handler=(
            lambda item: _promote_if_ready(
                repository,
                item["experiment_id"],
                int(item["pull_request_url"].rstrip("/").split("/")[-1]),
                JsonEvolutionStore(root),
            )
            if "auto-merge" in set(approve_rule)
            else False
        ),
    )
    budget = EvolutionBudget(
        max_experiments=max_experiments,
        max_gpu_minutes=max_gpu_minutes,
    )
    if once:
        typer.echo(str(worker.tick(baseline_ref=baseline, benchmark=benchmark, budget=budget)))
        return
    worker.daemon(
        baseline_ref=baseline,
        benchmark=benchmark,
        budget=budget,
        interval_seconds=interval,
    )


@app.command("promote")
def promote(
    experiment_id: str,
    pull_request: int = typer.Option(..., "--pr"),
    repository: str = typer.Option(..., "--repo"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
) -> None:
    """Merge an eligible PR only after deterministic policy and required checks pass."""
    if "auto-merge" not in set(approve_rule):
        raise PermissionError("--approve-rule auto-merge is required")
    settings = Settings.from_env()
    store = JsonEvolutionStore(settings.home / ".rea" / "evolution")
    if not _promote_if_ready(repository, experiment_id, pull_request, store):
        raise RuntimeError("promotion gates or required checks are not ready")
    typer.echo("promoted")


@app.command("canary")
def canary(
    candidate_ref: str,
    config: Path = typer.Option(..., "--config"),
    workspace: Path = typer.Option(Path("."), "--workspace"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
    max_percent: int = typer.Option(10, "--max-percent", min=1, max=25),
) -> None:
    """Run shadow validation and an explicitly approved, automatically reversible canary."""
    adapter = CommandDeploymentAdapter(
        DeploymentCommands.from_json(config),
        workspace.resolve(),
    )
    controller = CanaryController(adapter, max_canary_percent=max_percent)
    result = controller.run(
        candidate_ref,
        production_approval="production-canary" in set(approve_rule),
    )
    typer.echo(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


@app.command("status")
def status(
    root: Path = typer.Option(Path(".rea/evolution"), "--root"),
) -> None:
    typer.echo(json.dumps(StateStore(root / "state.json").load().__dict__, indent=2))


@app.command("lessons")
def lessons(
    repository: str = typer.Option(..., "--repo"),
    root: Path = typer.Option(Path(".rea/evolution"), "--root"),
) -> None:
    payload = [item.__dict__ for item in JsonEvolutionStore(root).lessons(repository)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
