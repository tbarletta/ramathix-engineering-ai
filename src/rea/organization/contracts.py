from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class WorkUnitState(StrEnum):
    PROPOSED = "proposed"
    READY = "ready"
    GOVERNANCE_APPROVAL_REQUIRED = "governance_approval_required"
    COST_APPROVAL_REQUIRED = "cost_approval_required"
    BLOCKED = "blocked"
    PUBLISHED = "published"


@dataclass
class Initiative:
    id: str
    title: str
    objective: str


@dataclass
class Project:
    id: str
    initiative_id: str
    title: str
    objective: str
    repository: str


@dataclass
class WorkUnit:
    id: str
    project_id: str
    title: str
    objective: str
    repository: str
    area: str
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    business_value: int = 3
    urgency: int = 3
    strategic_fit: int = 3
    effort: int = 3
    declared_risk: str = "medium"
    cost_impact: bool = False
    production_write: bool = False
    priority_score: int = 0
    priority_rank: int = 0
    governance: dict[str, Any] = field(default_factory=dict)
    state: WorkUnitState = WorkUnitState.PROPOSED
    github_issue: dict[str, Any] | None = None


@dataclass
class OrganizationPlan:
    id: str
    strategic_goal: str
    constraints: list[str]
    initiatives: list[Initiative]
    projects: list[Project]
    work_units: list[WorkUnit]
    created_at: str
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OrganizationPlan:
        return cls(
            id=str(payload["id"]),
            strategic_goal=str(payload["strategic_goal"]),
            constraints=list(payload.get("constraints", [])),
            initiatives=[Initiative(**item) for item in payload.get("initiatives", [])],
            projects=[Project(**item) for item in payload.get("projects", [])],
            work_units=[
                WorkUnit(
                    **{
                        **item,
                        "state": WorkUnitState(item.get("state", WorkUnitState.PROPOSED)),
                    }
                )
                for item in payload.get("work_units", [])
            ],
            created_at=str(payload["created_at"]),
            status=str(payload.get("status", "planned")),
        )
