from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from .contracts import EventKind, EvolutionEvent, EvolutionHypothesis


class AuditObserver:
    """Converts redacted audit records and external signals into normalized events."""

    EVENT_MAP = {
        "level6.blocked": EventKind.ITERATION_LIMIT,
        "ci.failed": EventKind.CI_FAILURE,
        "review.request_changes": EventKind.REVIEW_CHANGE,
        "production.regression": EventKind.PRODUCTION_REGRESSION,
        "human.correction": EventKind.HUMAN_CORRECTION,
    }

    def observe(self, records: list[dict], repository: str) -> list[EvolutionEvent]:
        events = []
        for record in records:
            kind = self.EVENT_MAP.get(str(record.get("event")))
            if kind is None:
                continue
            data = record.get("data") or {}
            summary = str(data.get("reason") or data.get("summary") or record["event"])
            fingerprint = str(data.get("fingerprint") or self.fingerprint(kind, summary))
            events.append(
                EvolutionEvent(
                    id=str(record.get("id") or uuid4().hex),
                    kind=kind,
                    repository=repository,
                    fingerprint=fingerprint,
                    summary=summary,
                    occurred_at=str(
                        record.get("timestamp") or datetime.now(UTC).isoformat()
                    ),
                    severity=str(data.get("severity", "medium")),
                    evidence=tuple(str(value) for value in data.get("evidence", [])),
                    attributes=dict(data),
                )
            )
        return events

    @staticmethod
    def fingerprint(kind: EventKind, summary: str) -> str:
        normalized = " ".join(summary.lower().split())
        return hashlib.sha256(f"{kind.value}:{normalized}".encode()).hexdigest()[:16]


class OpportunityDetector:
    def __init__(self, minimum_occurrences: int = 2) -> None:
        if minimum_occurrences < 1:
            raise ValueError("minimum_occurrences must be positive")
        self.minimum_occurrences = minimum_occurrences

    def detect(self, events: list[EvolutionEvent]) -> list[EvolutionHypothesis]:
        grouped: dict[tuple[str, str], list[EvolutionEvent]] = defaultdict(list)
        for event in events:
            grouped[(event.repository, event.fingerprint)].append(event)
        hypotheses = []
        for (repository, fingerprint), group in sorted(grouped.items()):
            if len(group) < self.minimum_occurrences:
                continue
            severity = "high" if any(item.severity in {"high", "critical"} for item in group) else "low"
            hypotheses.append(
                EvolutionHypothesis(
                    id=f"hyp-{fingerprint}",
                    repository=repository,
                    problem=f"{len(group)} recorrências: {group[-1].summary}",
                    hypothesis=(
                        "Uma mudança limitada e validada contra o benchmark reduzirá "
                        f"as recorrências de {fingerprint}"
                    ),
                    evidence=[item.id for item in group],
                    baseline=1 - min(len(group), 10) / 10,
                    target=min(1.0, 1 - min(len(group) - 1, 10) / 10),
                    risk=severity,
                    protected_capabilities=["safety", "task_success", "coverage"],
                )
            )
        return hypotheses
