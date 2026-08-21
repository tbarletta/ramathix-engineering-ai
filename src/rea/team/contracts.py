from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class EngineeringBrief:
    objective: str
    business_context: str
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    impacted_repositories: list[str] = field(default_factory=list)
    risk_level: str = RiskLevel.MEDIUM
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineeringTask:
    id: str
    owner_role: str
    description: str
    files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)


@dataclass
class TechnicalPlan:
    architecture_summary: str
    decision: str
    rationale: str
    tasks: list[EngineeringTask] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    rollback_strategy: list[str] = field(default_factory=list)
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileChangeProposal:
    path: str
    action: str
    rationale: str


@dataclass
class DeveloperProposal:
    summary: str
    files: list[FileChangeProposal] = field(default_factory=list)
    implementation_steps: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewFinding:
    severity: str
    category: str
    message: str


@dataclass
class CodeReview:
    decision: ReviewDecision
    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)
    missing_tests: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkPackage:
    repository: str
    issue_number: int
    knowledge_repository: str
    knowledge_root: str
    engineering_brief: EngineeringBrief
    technical_plan: TechnicalPlan
    developer_proposal: DeveloperProposal
    command_policy_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
