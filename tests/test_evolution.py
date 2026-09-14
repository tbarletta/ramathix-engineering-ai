from pathlib import Path
from types import SimpleNamespace

import pytest

from rea.audit import AuditLog
from rea.evolution import (
    AuditObserver,
    CanaryController,
    EvaluationEngine,
    EvolutionBudget,
    EvolutionHypothesis,
    EvolutionMetrics,
    EvolutionScorer,
    EvolutionWorkflow,
    ExperimentStatus,
    JsonEvolutionStore,
    ModelPromptOptimizer,
    OpportunityDetector,
    PromotionPolicy,
)


class Metrics:
    def __init__(self, values):
        self.values = values

    def measure(self, ref, benchmark):
        assert benchmark == "golden-v1"
        return self.values[ref]


def metrics(success=0.8, coverage=0.8, safety=0, regressions=0):
    return EvolutionMetrics(
        task_success_rate=success,
        first_pass_rate=success,
        coverage=coverage,
        safety_failures=safety,
        escaped_regressions=regressions,
    )


def test_evaluator_requires_improvement_without_regression():
    engine = EvaluationEngine(
        Metrics({"main": metrics(0.8), "candidate": metrics(0.9)}),
        minimum_delta=0.1,
    )
    result = engine.compare("main", "candidate", "golden-v1")
    assert result.passed
    assert result.delta > 0


def test_safety_regression_vetoes_higher_score():
    engine = EvaluationEngine(
        Metrics({"main": metrics(0.5), "candidate": metrics(1.0, safety=1)})
    )
    result = engine.compare("main", "candidate", "golden-v1")
    assert not result.passed
    assert "safety_failures" in result.critical_regressions


def test_metrics_validation_rejects_invalid_rate():
    with pytest.raises(ValueError):
        EvolutionScorer().score(metrics(success=1.1))


def test_detector_requires_repeated_evidence():
    records = [
        {
            "id": f"run-{number}",
            "event": "ci.failed",
            "timestamp": "2026-09-14T10:00:00Z",
            "data": {"summary": "typing failed", "fingerprint": "typing"},
        }
        for number in range(2)
    ]
    events = AuditObserver().observe(records, "owner/repo")
    hypotheses = OpportunityDetector(2).detect(events)
    assert len(hypotheses) == 1
    assert hypotheses[0].evidence == ["run-0", "run-1"]


def test_store_round_trip_and_lessons(tmp_path: Path):
    store = JsonEvolutionStore(tmp_path)
    assert store.lessons("owner/repo") == []


def test_promotion_blocks_governance_self_modification():
    evaluation = SimpleNamespace(passed=True, critical_regressions=())
    decision = PromotionPolicy().decide(
        evaluation,
        risk="low",
        changed_paths=["src/rea/evolution/promotion.py"],
        cost_impact=False,
    )
    assert not decision.allowed
    assert decision.action == "human_review"


class Deployment:
    def __init__(self, healthy):
        self.healthy = healthy
        self.rolled_back = []
        self.promoted = []

    def deploy_shadow(self, candidate_ref):
        return "shadow-1"

    def deploy_canary(self, candidate_ref, percent):
        assert percent <= 10
        return "canary-1"

    def metrics_healthy(self, deployment_id):
        return self.healthy.get(deployment_id, False)

    def promote(self, deployment_id):
        self.promoted.append(deployment_id)

    def rollback(self, deployment_id):
        self.rolled_back.append(deployment_id)


def test_canary_rolls_back_unhealthy_candidate():
    adapter = Deployment({"shadow-1": True, "canary-1": False})
    result = CanaryController(adapter).run("candidate", production_approval=True)
    assert result.state.value == "rolled_back"
    assert adapter.rolled_back == ["canary-1"]


def test_optimizer_explores_then_uses_rewards(tmp_path: Path):
    optimizer = ModelPromptOptimizer(tmp_path / "optimizer.json", ["a", "b"])
    assert optimizer.choose() == "a"
    optimizer.record("a", -1)
    assert optimizer.choose() == "b"
    optimizer.record("b", 1)
    assert optimizer.choose() == "b"


class Builder:
    def build(self, hypothesis, baseline_ref):
        return "candidate", ["src/service.py"]


class Publisher:
    def publish(self, experiment):
        return "https://github.com/owner/repo/pull/1"


def hypothesis():
    return EvolutionHypothesis(
        id="hyp-1",
        repository="owner/repo",
        problem="repeated failure",
        hypothesis="improve selection",
        evidence=["run-1", "run-2"],
        baseline=0.8,
        target=0.9,
        risk="low",
    )


def test_workflow_persists_success_and_lesson(tmp_path: Path):
    store = JsonEvolutionStore(tmp_path / "evolution")
    workflow = EvolutionWorkflow(
        evaluator=EvaluationEngine(
            Metrics({"main": metrics(0.7), "candidate": metrics(0.9)}),
            minimum_delta=0.1,
        ),
        builder=Builder(),
        publisher=Publisher(),
        store=store,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    experiment, decision = workflow.run(
        hypothesis(),
        baseline_ref="main",
        benchmark="golden-v1",
    )
    assert experiment.status is ExperimentStatus.PR_READY
    assert decision.allowed
    assert store.load_experiment(experiment.id).pull_request_url
    assert len(store.lessons("owner/repo")) == 1


def test_workflow_enforces_gpu_budget(tmp_path: Path):
    item = hypothesis()
    item.max_gpu_minutes = 60
    workflow = EvolutionWorkflow(
        evaluator=EvaluationEngine(Metrics({})),
        builder=Builder(),
        publisher=Publisher(),
        store=JsonEvolutionStore(tmp_path / "evolution"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(PermissionError):
        workflow.run(
            item,
            baseline_ref="main",
            benchmark="golden-v1",
            budget=EvolutionBudget(max_gpu_minutes=30),
        )
