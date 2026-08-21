from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    DOCUMENTED = "documented"
    INFERRED_HIGH = "inferred_high"
    INFERRED_MEDIUM = "inferred_medium"
    INFERRED_LOW = "inferred_low"


class EvidenceKind(StrEnum):
    FILE = "file"
    MANIFEST = "manifest"
    SOURCE = "source"
    CONFIG = "config"
    GIT = "git"
    INFERENCE = "inference"


@dataclass(frozen=True)
class Evidence:
    source: str
    kind: EvidenceKind
    detail: str | None = None


@dataclass(frozen=True)
class KnowledgeFact:
    category: str
    name: str
    value: str
    confidence: Confidence
    evidence: Evidence
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DependencyEdge:
    source: str
    target: str
    relation: str
    confidence: Confidence
    evidence: Evidence


@dataclass(frozen=True)
class SourceSymbol:
    path: str
    name: str
    kind: str
    language: str
    line: int


@dataclass
class GitSummary:
    branch: str | None = None
    commits_sampled: int = 0
    recent_commits: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RepositoryInventory:
    name: str
    root: str
    scanned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    file_count: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    manifests: list[str] = field(default_factory=list)
    facts: list[KnowledgeFact] = field(default_factory=list)
    dependencies: list[DependencyEdge] = field(default_factory=list)
    symbols: list[SourceSymbol] = field(default_factory=list)
    git: GitSummary = field(default_factory=GitSummary)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
