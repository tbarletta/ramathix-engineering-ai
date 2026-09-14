from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol
from uuid import uuid4

from ..audit import AuditLog
from .contracts import (
    EvolutionExperiment,
    EvolutionHypothesis,
    ExperimentStatus,
    Lesson,
    PromotionDecision,
)
from .evaluation import EvaluationEngine
from .promotion import PromotionPolicy
from .store import JsonEvolutionStore


class CandidateBuilder(Protocol):
    def build(self, hypothesis: EvolutionHypothesis, baseline_ref: str) -> tuple[str, list[str]]: ...


class PullRequestPublisher(Protocol):
    def publish(self, experiment: EvolutionExperiment) -> str: ...


@dataclass(frozen=True)
class EvolutionBudget:
    max_experiments: int = 1
    max_gpu_minutes: float = 30
    max_consecutive_failures: int = 3


class CircuitOpen(RuntimeError):
    pass


class EvolutionWorkflow:
    """Observe/detect is separate; this runs one measurable, resumable experiment."""

    def __init__(
        self,
        *,
        evaluator: EvaluationEngine,
        builder: CandidateBuilder,
        publisher: PullRequestPublisher,
        store: JsonEvolutionStore,
        audit: AuditLog,
        promotion: PromotionPolicy | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.builder = builder
        self.publisher = publisher
        self.store = store
        self.audit = audit
        self.promotion = promotion or PromotionPolicy()
        self.consecutive_failures = 0

    def run(
        self,
        hypothesis: EvolutionHypothesis,
        *,
        baseline_ref: str,
        benchmark: str,
        budget: EvolutionBudget | None = None,
        cost_approved: bool = False,
        approvals: set[str] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[EvolutionExperiment, PromotionDecision]:
        budget = budget or EvolutionBudget()
        report = on_progress or (lambda _message: None)
        if self.consecutive_failures >= budget.max_consecutive_failures:
            raise CircuitOpen("evolution circuit is open after consecutive failures")
        if hypothesis.max_gpu_minutes > budget.max_gpu_minutes and not cost_approved:
            raise PermissionError("experiment exceeds the approved GPU budget")

        experiment = EvolutionExperiment(
            id=f"exp-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8]}",
            hypothesis=hypothesis,
            baseline_ref=baseline_ref,
            status=ExperimentStatus.RUNNING,
        )
        self.store.save_experiment(experiment)
        self._audit("evolution.started", experiment, {})

        try:
            report("Building isolated candidate")
            candidate_ref, changed_paths = self.builder.build(hypothesis, baseline_ref)
            experiment.candidate_ref = candidate_ref
            self.store.save_experiment(experiment)

            report("Comparing candidate against baseline")
            evaluation = self.evaluator.compare(baseline_ref, candidate_ref, benchmark)
            experiment.evaluation = evaluation
            decision = self.promotion.decide(
                evaluation,
                risk=hypothesis.risk,
                changed_paths=changed_paths,
                cost_impact=hypothesis.max_gpu_minutes > budget.max_gpu_minutes,
                approvals=approvals,
            )
            if not evaluation.passed:
                experiment.status = ExperimentStatus.REJECTED
                experiment.reason = "; ".join(decision.reasons)
                self.consecutive_failures += 1
            else:
                experiment.status = ExperimentStatus.PR_READY
                experiment.pull_request_url = self.publisher.publish(experiment)
                experiment.reason = decision.action
                self.consecutive_failures = 0
            self.store.save_experiment(experiment)
            self._learn(experiment)
            self._audit("evolution.completed", experiment, {"decision": decision.action})
            return experiment, decision
        except Exception as exc:
            experiment.status = ExperimentStatus.BLOCKED
            experiment.reason = str(exc)
            self.store.save_experiment(experiment)
            self.consecutive_failures += 1
            self._audit("evolution.failed", experiment, {"reason": str(exc)})
            raise

    def _learn(self, experiment: EvolutionExperiment) -> None:
        evaluation = experiment.evaluation
        if evaluation is None:
            return
        outcome = "improved" if evaluation.passed else "regressed"
        self.store.append_lesson(
            Lesson(
                id=f"lesson-{uuid4().hex[:12]}",
                repository=experiment.hypothesis.repository,
                fingerprint=experiment.hypothesis.id,
                statement=(
                    f"Candidate {experiment.candidate_ref} {outcome}; "
                    f"score delta {evaluation.delta:+.4f}"
                ),
                confidence=min(0.99, 0.6 + abs(evaluation.delta) / 10),
                evidence=tuple(experiment.hypothesis.evidence),
                outcome=outcome,
            )
        )

    def _audit(self, event: str, experiment: EvolutionExperiment, data: dict) -> None:
        self.audit.write(
            event,
            actor="evolution_controller",
            data={"experiment": experiment.id, "status": experiment.status.value, **data},
        )
