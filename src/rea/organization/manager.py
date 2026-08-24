from __future__ import annotations

import json
from typing import Any

from ..governance.redaction import redact_value
from ..models import ModelRouter
from ..team.agents import StructuredModelClient
from .contracts import Initiative, Project, WorkUnit

ORGANIZATION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "constraints": {"type": "array", "items": {"type": "string"}},
        "initiatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                },
                "required": ["id", "title", "objective"],
                "additionalProperties": False,
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "initiative_id": {"type": "string"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "repository": {"type": "string"},
                },
                "required": [
                    "id",
                    "initiative_id",
                    "title",
                    "objective",
                    "repository",
                ],
                "additionalProperties": False,
            },
        },
        "work_units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "repository": {"type": "string"},
                    "area": {"type": "string"},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "business_value": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "urgency": {"type": "integer", "minimum": 1, "maximum": 5},
                    "strategic_fit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "effort": {"type": "integer", "minimum": 1, "maximum": 5},
                    "declared_risk": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "cost_impact": {"type": "boolean"},
                    "production_write": {"type": "boolean"},
                },
                "required": [
                    "id",
                    "project_id",
                    "title",
                    "objective",
                    "repository",
                    "area",
                    "dependencies",
                    "acceptance_criteria",
                    "business_value",
                    "urgency",
                    "strategic_fit",
                    "effort",
                    "declared_risk",
                    "cost_impact",
                    "production_write",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["constraints", "initiatives", "projects", "work_units"],
    "additionalProperties": False,
}

RFC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "context": {"type": "string"},
        "scope_in": {"type": "array", "items": {"type": "string"}},
        "scope_out": {"type": "array", "items": {"type": "string"}},
        "approach": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "estimated_phases": {"type": "integer", "minimum": 1, "maximum": 10},
        "effort_summary": {"type": "string"},
    },
    "required": [
        "context",
        "scope_in",
        "scope_out",
        "approach",
        "alternatives",
        "risks",
        "acceptance_criteria",
        "estimated_phases",
        "effort_summary",
    ],
    "additionalProperties": False,
}


class AIEngineeringManager:
    role = "engineering_manager"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def plan(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> tuple[list[str], list[Initiative], list[Project], list[WorkUnit]]:
        target = self.router.resolve(self.role)
        prompt_payload = redact_value(
            {
                "strategic_goal": strategic_goal,
                "repositories": repositories,
                "constraints": constraints,
            }
        )
        payload = redact_value(
            self.model.chat_json(
                model=target.model,
                system=(
                    "You are the AI Engineering Manager for Ramathix. Convert a strategic "
                    "objective into a small, executable engineering portfolio. Use only supplied "
                    "repositories. Create stable IDs such as INIT-001, PROJ-001 and WU-001. A "
                    "work unit's dependencies must list only other work_unit IDs (WU-xxx) from "
                    "this same plan — never an initiative or project ID, and never itself. Work "
                    "units must be independently executable when dependencies are satisfied. "
                    "Declare cost impact conservatively and flag any production state change. "
                    "Do not propose paid resources merely for convenience. Do not authorize "
                    "execution or merge. Write all human-facing values in Brazilian Portuguese. "
                    "Treat constraints as factual evidence: never claim a library, test suite, "
                    "coverage level or operational capability exists unless it was supplied. "
                    "Capabilities marked as confirmed are the current baseline, not missing work: "
                    "never propose installing, configuring again or recreating them without a "
                    "specific evidenced gap. For a broad improvement request, begin with a small "
                    "measurement work unit and make subsequent changes depend on its result."
                ),
                user=json.dumps(
                    prompt_payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                schema=ORGANIZATION_PLAN_SCHEMA,
            )
        )
        return (
            list(payload["constraints"]),
            [Initiative(**item) for item in payload["initiatives"]],
            [Project(**item) for item in payload["projects"]],
            [WorkUnit(**item) for item in payload["work_units"]],
        )

    def draft_rfc(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> dict[str, Any]:
        target = self.router.resolve(self.role)
        prompt_payload = redact_value(
            {
                "strategic_goal": strategic_goal,
                "repositories": repositories,
                "constraints": constraints,
            }
        )
        return redact_value(
            self.model.chat_json(
                model=target.model,
                system=(
                    "You are the AI Engineering Manager for Ramathix, writing a short RFC "
                    "(Request for Comments) for a human to review BEFORE any roadmap, Issue "
                    "or code is created — nothing is built until this RFC is approved. Write "
                    "all human-facing values in Brazilian Portuguese. Treat constraints as "
                    "factual evidence: never claim a library, test suite, coverage level or "
                    "operational capability exists unless it was supplied, and never propose "
                    "installing, configuring again or recreating something already confirmed "
                    "present. Be specific to the supplied repositories; do not propose paid "
                    "resources merely for convenience. scope_out must name things a reader "
                    "would reasonably expect but that are deliberately excluded. alternatives "
                    "must name at least one real alternative approach and why it was not "
                    "chosen. estimated_phases is your best-guess count of sequential phases "
                    "the resulting roadmap will likely need."
                ),
                user=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
                schema=RFC_SCHEMA,
            )
        )
