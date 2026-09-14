from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    CI_FAILURE = "ci_failure"
    REVIEW_CHANGE = "review_change"
    ITERATION_LIMIT = "iteration_limit"
    PRODUCTION_REGRESSION = "production_regression"
    HUMAN_CORRECTION = "human_correction"


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    RUNNING = "running"
    REJECTED = "rejected"
    PR_READY = "pr_ready"
    CANARY = "canary"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class EvolutionEvent:
    id: str
    kind: EventKind
    repository: str
    fingerprint: str
    summary: str
    occurred_at: str
    severity: str = "medium"
    evidence: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionHypothesis:
    id: str
    repository: str
    problem: str
    hypothesis: str
    evidence: list[str]
    baseline: float
    target: float
    risk: str
    max_iterations: int = 3
    max_gpu_minutes: int = 30
    protected_capabilities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvolutionMetrics:
    task_success_rate: float = 0.0
    first_pass_rate: float = 0.0
    escaped_regressions: int = 0
    human_interventions: int = 0
    coverage: float = 0.0
    latency_seconds: float = 0.0
    gpu_minutes: float = 0.0
    safety_failures: int = 0

    def validate(self) -> None:
        for name in ("task_success_rate", "first_pass_rate", "coverage"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in (
            "escaped_regressions",
            "human_interventions",
            "latency_seconds",
            "gpu_minutes",
            "safety_failures",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class EvaluationResult:
    baseline: EvolutionMetrics
    candidate: EvolutionMetrics
    baseline_score: float
    candidate_score: float
    delta: float
    critical_regressions: tuple[str, ...]
    passed: bool


@dataclass
class EvolutionExperiment:
    id: str
    hypothesis: EvolutionHypothesis
    baseline_ref: str
    candidate_ref: str | None = None
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    evaluation: EvaluationResult | None = None
    pull_request_url: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Lesson:
    id: str
    repository: str
    fingerprint: str
    statement: str
    confidence: float
    evidence: tuple[str, ...]
    outcome: str


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    action: str
    reasons: tuple[str, ...]
