from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .contracts import EvaluationResult, PromotionDecision


PROTECTED_PATHS = (
    "src/rea/governance/",
    "src/rea/policy.py",
    "src/rea/evolution/promotion.py",
    "config/policies/",
    ".github/",
)


class PromotionPolicy:
    """Deterministic gate. A candidate cannot authorize its own promotion."""

    def decide(
        self,
        evaluation: EvaluationResult,
        *,
        risk: str,
        changed_paths: list[str],
        cost_impact: bool,
        approvals: set[str] | None = None,
    ) -> PromotionDecision:
        approvals = approvals or set()
        reasons = []
        protected = [
            path for path in changed_paths if any(path.startswith(root) for root in PROTECTED_PATHS)
        ]
        if not evaluation.passed:
            reasons.append("candidate did not beat the baseline")
        if evaluation.critical_regressions:
            reasons.append("critical capability regression")
        if cost_impact:
            reasons.append("cost impact requires explicit approval")
        if protected:
            reasons.append("candidate changes its promotion or governance boundary")
        if risk in {"high", "critical"}:
            reasons.append(f"{risk} risk cannot be auto-promoted")

        if reasons:
            return PromotionDecision(
                allowed=False,
                action="human_review" if evaluation.passed else "reject",
                reasons=tuple(reasons),
            )
        if risk == "medium" and "tech-lead" not in approvals:
            return PromotionDecision(False, "tech_lead_review", ("medium risk",))
        return PromotionDecision(True, "auto_merge", ())


class DeploymentState(StrEnum):
    SHADOW = "shadow"
    CANARY = "canary"
    HEALTHY = "healthy"
    ROLLED_BACK = "rolled_back"
    PROMOTED = "promoted"


class DeploymentAdapter(Protocol):
    def deploy_shadow(self, candidate_ref: str) -> str: ...
    def deploy_canary(self, candidate_ref: str, percent: int) -> str: ...
    def metrics_healthy(self, deployment_id: str) -> bool: ...
    def promote(self, deployment_id: str) -> None: ...
    def rollback(self, deployment_id: str) -> None: ...


@dataclass(frozen=True)
class CanaryResult:
    deployment_id: str
    state: DeploymentState
    reason: str


class CanaryController:
    """Bounded production adapter: canary first, automatic rollback on unhealthy metrics."""

    def __init__(self, adapter: DeploymentAdapter, max_canary_percent: int = 10) -> None:
        if not 1 <= max_canary_percent <= 25:
            raise ValueError("max_canary_percent must be between 1 and 25")
        self.adapter = adapter
        self.max_canary_percent = max_canary_percent

    def run(self, candidate_ref: str, *, production_approval: bool = False) -> CanaryResult:
        shadow = self.adapter.deploy_shadow(candidate_ref)
        if not self.adapter.metrics_healthy(shadow):
            self.adapter.rollback(shadow)
            return CanaryResult(shadow, DeploymentState.ROLLED_BACK, "shadow unhealthy")
        if not production_approval:
            return CanaryResult(shadow, DeploymentState.HEALTHY, "production approval required")
        canary = self.adapter.deploy_canary(candidate_ref, self.max_canary_percent)
        if not self.adapter.metrics_healthy(canary):
            self.adapter.rollback(canary)
            return CanaryResult(canary, DeploymentState.ROLLED_BACK, "canary unhealthy")
        self.adapter.promote(canary)
        return CanaryResult(canary, DeploymentState.PROMOTED, "canary healthy")
