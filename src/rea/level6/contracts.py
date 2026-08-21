from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FileMutation:
    path: str
    action: str
    content: str | None = None


@dataclass
class CodingIteration:
    summary: str
    changes: list[FileMutation] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    cost_impact: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationCommand:
    command: str
    returncode: int
    stdout: str
    stderr: str


@dataclass
class IterationRecord:
    number: int
    coding: CodingIteration
    validation: list[ValidationCommand] = field(default_factory=list)
    diff: str = ""
    review: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Level6Result:
    issue_number: int
    branch: str
    pull_request_url: str | None
    iterations: list[IterationRecord] = field(default_factory=list)
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
