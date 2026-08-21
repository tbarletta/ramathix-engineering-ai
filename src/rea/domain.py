from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    COST_APPROVAL = "cost_approval"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    rule_id: str | None
    reason: str


@dataclass(frozen=True)
class ModelTarget:
    alias: str
    provider: str
    model: str
    context_window: int


@dataclass(frozen=True)
class IssueWorkUnit:
    repository: str
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...] = ()


@dataclass
class TechLeadPlan:
    summary: str
    affected_areas: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
