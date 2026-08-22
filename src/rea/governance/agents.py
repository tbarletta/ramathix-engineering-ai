from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import ModelRouter
from ..team.agents import StructuredModelClient
from ..team.context import TeamKnowledgeContext
from .contracts import GovernanceFinding, GovernanceRiskLevel, SpecialistAssessment
from .redaction import redact_value

GOVERNANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "category": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["severity", "category", "message"],
                "additionalProperties": False,
            },
        },
        "cost_impact": {"type": "boolean"},
        "production_write": {"type": "boolean"},
        "requires_human": {"type": "boolean"},
    },
    "required": [
        "summary",
        "findings",
        "cost_impact",
        "production_write",
        "requires_human",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GovernanceAgentProfile:
    role: str
    mission: str


class GovernanceSpecialistAgent:
    def __init__(
        self,
        *,
        router: ModelRouter,
        model: StructuredModelClient,
        profile: GovernanceAgentProfile,
    ) -> None:
        self.router = router
        self.model = model
        self.profile = profile

    def assess(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
    ) -> SpecialistAssessment:
        target = self.router.resolve(self.profile.role)
        prompt_payload = redact_value(
            {
                "work_package": work_package,
                "knowledge": context.compact,
            }
        )
        payload = redact_value(
            self.model.chat_json(
                model=target.model,
                system=(
                    f"You are the {self.profile.role} governance specialist. "
                    f"{self.profile.mission} "
                    "Assess only the supplied plan and repository knowledge. "
                    "Do not authorize execution. Escalation is allowed; lowering deterministic "
                    "risk is not. Mark production_write for any proposed production state change "
                    "and cost_impact whenever spend may increase."
                ),
                user=json.dumps(
                    prompt_payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                schema=GOVERNANCE_SCHEMA,
            )
        )
        return SpecialistAssessment(
            role=self.profile.role,
            summary=str(payload["summary"]),
            findings=[
                GovernanceFinding(
                    severity=GovernanceRiskLevel(item["severity"]),
                    category=str(item["category"]),
                    message=str(item["message"]),
                )
                for item in payload["findings"]
            ],
            cost_impact=bool(payload["cost_impact"]),
            production_write=bool(payload["production_write"]),
            requires_human=bool(payload["requires_human"]),
        )


class SecurityAgent(GovernanceSpecialistAgent):
    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        super().__init__(
            router=router,
            model=model,
            profile=GovernanceAgentProfile(
                role="security",
                mission=(
                    "Review authentication, authorization, secrets, privacy, input validation, "
                    "data exposure, dependency risk and privilege boundaries."
                ),
            ),
        )


class PerformanceAgent(GovernanceSpecialistAgent):
    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        super().__init__(
            router=router,
            model=model,
            profile=GovernanceAgentProfile(
                role="performance",
                mission=(
                    "Review latency, throughput, concurrency, memory, CPU, database access, "
                    "queues, caching and regression risk."
                ),
            ),
        )


class CloudArchitectAgent(GovernanceSpecialistAgent):
    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        super().__init__(
            router=router,
            model=model,
            profile=GovernanceAgentProfile(
                role="cloud_architect",
                mission=(
                    "Review infrastructure boundaries, reliability, networking, deployment, "
                    "capacity, cloud resources and operational blast radius."
                ),
            ),
        )


class FinOpsAgent(GovernanceSpecialistAgent):
    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        super().__init__(
            router=router,
            model=model,
            profile=GovernanceAgentProfile(
                role="finops",
                mission=(
                    "Review direct and indirect cost impact, paid services, capacity changes, "
                    "storage, compute, model/provider usage and recurring spend."
                ),
            ),
        )
