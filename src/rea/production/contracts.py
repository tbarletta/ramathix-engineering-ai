from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SignalKind(StrEnum):
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    DEPLOYMENT = "deployment"
    GIT_CHANGE = "git_change"
    DATABASE = "database"
    QUEUE = "queue"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True)
class ProductionSignal:
    evidence_id: str
    timestamp: str
    kind: SignalKind
    source: str
    service: str
    summary: str
    severity: str = "info"
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True)
class IncidentRequest:
    incident_id: str
    title: str
    service: str
    description: str
    started_at: str | None = None


@dataclass(frozen=True)
class TimelineEvent:
    timestamp: str
    event: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootCauseHypothesis:
    statement: str
    confidence: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemediationProposal:
    action: str
    risk_level: str
    cost_impact: bool
    requires_write: bool


@dataclass
class IncidentAnalysis:
    incident_id: str
    service: str
    impact_summary: str
    timeline: list[TimelineEvent] = field(default_factory=list)
    hypotheses: list[RootCauseHypothesis] = field(default_factory=list)
    remediations: list[RemediationProposal] = field(default_factory=list)
    preventive_actions: list[str] = field(default_factory=list)
    evidence_count: int = 0
    cost_approval_required: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
