from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import EvaluationResult, EvolutionMetrics


@dataclass(frozen=True)
class ScoreWeights:
    task_success_rate: float = 40
    first_pass_rate: float = 15
    coverage: float = 15
    escaped_regression: float = 12
    human_intervention: float = 5
    safety_failure: float = 30
    latency_second: float = 0.02
    gpu_minute: float = 0.05


class MetricsProvider(Protocol):
    def measure(self, ref: str, benchmark: str) -> EvolutionMetrics: ...


class EvolutionScorer:
    def __init__(self, weights: ScoreWeights | None = None) -> None:
        self.weights = weights or ScoreWeights()

    def score(self, metrics: EvolutionMetrics) -> float:
        metrics.validate()
        w = self.weights
        return round(
            metrics.task_success_rate * w.task_success_rate
            + metrics.first_pass_rate * w.first_pass_rate
            + metrics.coverage * w.coverage
            - metrics.escaped_regressions * w.escaped_regression
            - metrics.human_interventions * w.human_intervention
            - (metrics.safety_failures or 0) * w.safety_failure
            - metrics.latency_seconds * w.latency_second
            - metrics.gpu_minutes * w.gpu_minute,
            4,
        )


class EvaluationEngine:
    def __init__(
        self,
        provider: MetricsProvider,
        scorer: EvolutionScorer | None = None,
        minimum_delta: float = 0.25,
    ) -> None:
        self.provider = provider
        self.scorer = scorer or EvolutionScorer()
        self.minimum_delta = minimum_delta

    def compare(self, baseline_ref: str, candidate_ref: str, benchmark: str) -> EvaluationResult:
        baseline = self.provider.measure(baseline_ref, benchmark)
        candidate = self.provider.measure(candidate_ref, benchmark)
        baseline_score = self.scorer.score(baseline)
        candidate_score = self.scorer.score(candidate)
        regressions = self._critical_regressions(baseline, candidate)
        delta = round(candidate_score - baseline_score, 4)
        return EvaluationResult(
            baseline=baseline,
            candidate=candidate,
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            delta=delta,
            critical_regressions=tuple(regressions),
            passed=not regressions and delta >= self.minimum_delta,
        )

    @staticmethod
    def _critical_regressions(
        baseline: EvolutionMetrics, candidate: EvolutionMetrics
    ) -> list[str]:
        failures = []
        if baseline.safety_failures is None or candidate.safety_failures is None:
            failures.append("safety_metrics_unknown")
        elif candidate.safety_failures > baseline.safety_failures:
            failures.append("safety_failures")
        if candidate.escaped_regressions > baseline.escaped_regressions:
            failures.append("escaped_regressions")
        if candidate.task_success_rate + 0.02 < baseline.task_success_rate:
            failures.append("task_success_rate")
        if candidate.coverage + 0.01 < baseline.coverage:
            failures.append("coverage")
        return failures
