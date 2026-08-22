from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class GovernanceRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GovernanceDecision(StrEnum):
    AUTONOMOUS = "autonomous"
    GOVERNED = "governed"
    TECH_LEAD_APPROVAL = "tech_lead_approval"
    HUMAN_APPROVAL = "human_approval"
    COST_APPROVAL = "cost_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class GovernanceFinding:
    severity: GovernanceRiskLevel
    category: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass
class SpecialistAssessment:
    role: str
    summary: str
    findings: list[GovernanceFinding] = field(default_factory=list)
    cost_impact: bool = False
    production_write: bool = False
    requires_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
            "cost_impact": self.cost_impact,
            "production_write": self.production_write,
            "requires_human": self.requires_human,
        }


@dataclass
class GovernanceAssessment:
    score: int
    risk_level: GovernanceRiskLevel
    decision: GovernanceDecision
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    specialists: list[SpecialistAssessment] = field(default_factory=list)
    cost_impact: bool = False
    production_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "risk_level": self.risk_level.value,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "signals": list(self.signals),
            "specialists": [item.to_dict() for item in self.specialists],
            "cost_impact": self.cost_impact,
            "production_write": self.production_write,
        }
