from __future__ import annotations

from typing import Any

from ..audit import AuditLog
from ..domain import Decision
from ..execution import ExecutionApprovalRequired, ExecutionDenied
from ..models import ModelRouter
from ..team import CostApprovalRequired
from ..team.agents import StructuredModelClient
from ..team.context import TeamKnowledgeContext
from .agents import CloudArchitectAgent, FinOpsAgent, PerformanceAgent, SecurityAgent
from .contracts import GovernanceAssessment, GovernanceDecision
from .risk import RiskEngine


class GovernanceApprovalRequired(ExecutionApprovalRequired):
    def __init__(self, required: str, assessment: GovernanceAssessment) -> None:
        self.required = required
        self.assessment = assessment
        rule_id = (
            "governance-human"
            if required == "human"
            else "governance-tech-lead"
        )
        super().__init__(
            decision=Decision.ASK,
            rule_id=rule_id,
            argv=["governance", "approve", required],
            reason=(
                f"advanced governance classified risk as {assessment.risk_level.value} "
                f"with score {assessment.score}"
            ),
        )


class GovernanceBlocked(ExecutionDenied):
    def __init__(self, assessment: GovernanceAssessment) -> None:
        self.assessment = assessment
        super().__init__(
            "advanced governance blocked execution: production writes remain disabled"
        )


class AdvancedGovernanceWorkflow:
    def __init__(
        self,
        *,
        router: ModelRouter,
        model: StructuredModelClient,
        audit: AuditLog,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self.audit = audit
        self.risk_engine = risk_engine or RiskEngine()
        self.agents = [
            SecurityAgent(router, model),
            PerformanceAgent(router, model),
            CloudArchitectAgent(router, model),
            FinOpsAgent(router, model),
        ]

    def assess(
        self,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
    ) -> GovernanceAssessment:
        specialists = []
        for agent in self.agents:
            assessment = agent.assess(work_package=work_package, context=context)
            specialists.append(assessment)
            self.audit.write(
                "governance.specialist_assessed",
                actor=assessment.role,
                data=assessment.to_dict(),
            )

        result = self.risk_engine.assess(work_package, specialists)
        self.audit.write(
            "governance.assessed",
            actor="risk_engine",
            data=result.to_dict(),
        )
        return result

    def enforce(
        self,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        *,
        approvals: set[str] | None = None,
    ) -> GovernanceAssessment:
        approvals = approvals or set()
        assessment = self.assess(work_package, context)

        if assessment.decision is GovernanceDecision.BLOCKED:
            raise GovernanceBlocked(assessment)
        if assessment.decision is GovernanceDecision.COST_APPROVAL:
            raise CostApprovalRequired("advanced_governance", assessment.to_dict())
        if assessment.decision is GovernanceDecision.HUMAN_APPROVAL:
            if "human" not in approvals:
                raise GovernanceApprovalRequired("human", assessment)
        if assessment.decision is GovernanceDecision.TECH_LEAD_APPROVAL:
            if not ({"tech-lead", "human"} & approvals):
                raise GovernanceApprovalRequired("tech-lead", assessment)

        self.audit.write(
            "governance.gate_passed",
            actor="risk_engine",
            data={
                "decision": assessment.decision.value,
                "risk_level": assessment.risk_level.value,
                "score": assessment.score,
                "approvals": sorted(approvals),
            },
        )
        return assessment
