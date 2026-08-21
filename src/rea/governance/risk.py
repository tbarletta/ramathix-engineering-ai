from __future__ import annotations

import json
from typing import Any

from .contracts import (
    GovernanceAssessment,
    GovernanceDecision,
    GovernanceRiskLevel,
    SpecialistAssessment,
)


_SECURITY_MARKERS = (
    "auth",
    "oauth",
    "jwt",
    "credential",
    "password",
    "secret",
    "encryption",
    "permission",
    "access control",
    "payment",
    "checkout",
    "pix",
    "card",
    "kyc",
)
_DATA_MARKERS = (
    "migration",
    "schema.prisma",
    "alembic",
    "alter table",
    "drop column",
    "drop table",
    "backfill",
    "database schema",
)
_INFRA_MARKERS = (
    "terraform",
    "kubernetes",
    "k8s",
    "deployment",
    "replicas",
    "instance",
    "cluster",
    "nginx",
    "load balancer",
)
_PERFORMANCE_MARKERS = (
    "latency",
    "throughput",
    "concurrency",
    "deadlock",
    "cache",
    "memory",
    "cpu",
    "index",
    "query performance",
)
_PRODUCTION_WRITE_MARKERS = (
    "production write",
    "deploy to production",
    "deploy to prod",
    "restart production",
    "restart prod",
    "rollback production",
    "rollback prod",
    "scale production",
    "scale prod",
    "reprocess production",
    "reprocess prod",
    "clear production cache",
    "clear prod cache",
    "terraform apply to production",
    "terraform apply in production",
    "terraform apply to prod",
    "terraform apply in prod",
    "kubectl apply to production",
    "kubectl apply in production",
    "kubectl apply to prod",
    "kubectl apply in prod",
)
_COST_MARKERS = (
    "provision",
    "increase capacity",
    "larger instance",
    "bigger instance",
    "upgrade plan",
    "increase replicas",
    "add replicas",
    "paid service",
    "purchase",
    "new gpu",
    "additional storage",
    "scale production",
    "terraform apply",
)
_SEVERITY_FLOOR = {
    GovernanceRiskLevel.LOW: 0,
    GovernanceRiskLevel.MEDIUM: 25,
    GovernanceRiskLevel.HIGH: 50,
    GovernanceRiskLevel.CRITICAL: 75,
}
_DECLARED_RISK_SCORE = {
    "low": 0,
    "medium": 25,
    "high": 50,
    "critical": 75,
}


class RiskEngine:
    """Deterministic risk aggregation. Specialist agents may only raise risk."""

    def assess(
        self,
        work_package: dict[str, Any],
        specialists: list[SpecialistAssessment],
    ) -> GovernanceAssessment:
        text = json.dumps(work_package, ensure_ascii=False, sort_keys=True).lower()
        score = self._declared_risk_score(work_package)
        signals: list[str] = []
        reasons: list[str] = []

        score += self._marker_score(
            text,
            _SECURITY_MARKERS,
            30,
            "security-sensitive change",
            signals,
        )
        score += self._marker_score(
            text,
            _DATA_MARKERS,
            25,
            "data/schema change",
            signals,
        )
        score += self._marker_score(
            text,
            _INFRA_MARKERS,
            20,
            "infrastructure/cloud change",
            signals,
        )
        score += self._marker_score(
            text,
            _PERFORMANCE_MARKERS,
            10,
            "performance-sensitive change",
            signals,
        )

        production_write = any(marker in text for marker in _PRODUCTION_WRITE_MARKERS)
        if production_write:
            score = max(score, 100)
            signals.append("production write/state change")

        deterministic_cost = any(marker in text for marker in _COST_MARKERS)
        cost_impact = deterministic_cost or self._declared_cost(work_package)
        if deterministic_cost:
            signals.append("potential cost increase")

        requires_human = False
        for assessment in specialists:
            if assessment.findings:
                max_severity = max(
                    (item.severity for item in assessment.findings),
                    key=lambda item: _SEVERITY_FLOOR[item],
                )
                score = max(score, _SEVERITY_FLOOR[max_severity])
            cost_impact = cost_impact or assessment.cost_impact
            production_write = production_write or assessment.production_write
            requires_human = requires_human or assessment.requires_human
            for finding in assessment.findings:
                if finding.severity in {
                    GovernanceRiskLevel.HIGH,
                    GovernanceRiskLevel.CRITICAL,
                }:
                    reasons.append(
                        f"{assessment.role}: {finding.severity.value}/{finding.category}"
                    )

        if requires_human:
            score = max(score, 50)
            reasons.append("specialist requested human review")
        if production_write:
            score = max(score, 100)
        score = min(score, 100)

        level = self._level(score)
        if production_write:
            decision = GovernanceDecision.BLOCKED
            reasons.append("production writes remain disabled")
        elif cost_impact:
            decision = GovernanceDecision.COST_APPROVAL
            reasons.append("cost impact requires human approval")
        elif requires_human or level is GovernanceRiskLevel.CRITICAL:
            decision = GovernanceDecision.HUMAN_APPROVAL
        elif level is GovernanceRiskLevel.HIGH:
            decision = GovernanceDecision.TECH_LEAD_APPROVAL
        elif level is GovernanceRiskLevel.MEDIUM:
            decision = GovernanceDecision.GOVERNED
        else:
            decision = GovernanceDecision.AUTONOMOUS

        return GovernanceAssessment(
            score=score,
            risk_level=level,
            decision=decision,
            reasons=list(dict.fromkeys(reasons)),
            signals=list(dict.fromkeys(signals)),
            specialists=specialists,
            cost_impact=cost_impact,
            production_write=production_write,
        )

    @staticmethod
    def _declared_risk_score(work_package: dict[str, Any]) -> int:
        risk = str(
            work_package.get("engineering_brief", {}).get("risk_level", "medium")
        ).lower()
        return _DECLARED_RISK_SCORE.get(risk, 25)

    @staticmethod
    def _declared_cost(work_package: dict[str, Any]) -> bool:
        sections = (
            work_package.get("engineering_brief", {}),
            work_package.get("technical_plan", {}),
            work_package.get("developer_proposal", {}),
        )
        return any(bool(section.get("cost_impact")) for section in sections)

    @staticmethod
    def _marker_score(
        text: str,
        markers: tuple[str, ...],
        score: int,
        signal: str,
        signals: list[str],
    ) -> int:
        if not any(marker in text for marker in markers):
            return 0
        signals.append(signal)
        return score

    @staticmethod
    def _level(score: int) -> GovernanceRiskLevel:
        if score >= 75:
            return GovernanceRiskLevel.CRITICAL
        if score >= 50:
            return GovernanceRiskLevel.HIGH
        if score >= 25:
            return GovernanceRiskLevel.MEDIUM
        return GovernanceRiskLevel.LOW
