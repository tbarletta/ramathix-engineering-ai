from __future__ import annotations

import json
from typing import Any

from ..models import ModelRouter
from ..team.agents import StructuredModelClient
from .contracts import (
    IncidentAnalysis,
    IncidentRequest,
    ProductionSignal,
    RemediationProposal,
    RootCauseHypothesis,
    TimelineEvent,
)
from .redaction import redact_value

INCIDENT_SCHEMA = {
    "type": "object",
    "properties": {
        "impact_summary": {"type": "string"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "event": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["timestamp", "event", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "confidence", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "remediations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "cost_impact": {"type": "boolean"},
                    "requires_write": {"type": "boolean"},
                },
                "required": [
                    "action",
                    "risk_level",
                    "cost_impact",
                    "requires_write",
                ],
                "additionalProperties": False,
            },
        },
        "preventive_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "impact_summary",
        "timeline",
        "hypotheses",
        "remediations",
        "preventive_actions",
    ],
    "additionalProperties": False,
}


_WRITE_MARKERS = (
    "restart",
    "scale",
    "reprocess",
    "delete",
    "create",
    "update",
    "write",
    "clear cache",
    "flush",
    "terminate",
    "deploy",
    "rollback",
)
_COST_MARKERS = (
    "scale",
    "provision",
    "increase capacity",
    "larger instance",
    "bigger instance",
    "upgrade plan",
    "increase replicas",
    "add replicas",
    "purchase",
    "paid service",
)


class IncidentSREAgent:
    role = "devops_sre"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def analyze(
        self,
        request: IncidentRequest,
        signals: list[ProductionSignal],
    ) -> IncidentAnalysis:
        target = self.router.resolve(self.role)
        evidence = []
        for signal in signals:
            payload = redact_value(signal.to_dict())
            payload["evidence_id"] = signal.evidence_id
            evidence.append(payload)
        incident = redact_value(
            {
                "id": request.incident_id,
                "title": request.title,
                "service": request.service,
                "description": request.description,
                "started_at": request.started_at,
            }
        )
        data = self.model.chat_json(
            model=target.model,
            system=(
                "You are the Incident/SRE Agent. Analyze only supplied production evidence. "
                "Correlate events temporally, identify impact and propose root-cause hypotheses. "
                "Every hypothesis must reference evidence IDs. Remediations are proposals only; "
                "never claim an action was executed. Mark cost_impact when cost may increase. "
                "Mark requires_write for restart, scale, deploy, rollback, reprocess, cache clear, "
                "database/queue mutation or any production state change."
            ),
            user=json.dumps(
                {"incident": incident, "evidence": evidence},
                ensure_ascii=False,
                indent=2,
            ),
            schema=INCIDENT_SCHEMA,
        )
        sanitized = redact_value(data)
        return self._validate(request, signals, sanitized)

    def _validate(
        self,
        request: IncidentRequest,
        signals: list[ProductionSignal],
        data: dict[str, Any],
    ) -> IncidentAnalysis:
        known = {item.evidence_id for item in signals}
        timeline = [
            TimelineEvent(
                timestamp=item["timestamp"],
                event=item["event"],
                evidence_ids=tuple(self._known_refs(item["evidence_ids"], known)),
            )
            for item in data["timeline"]
        ]
        hypotheses = [
            RootCauseHypothesis(
                statement=item["statement"],
                confidence=item["confidence"],
                evidence_ids=tuple(self._known_refs(item["evidence_ids"], known)),
            )
            for item in data["hypotheses"]
        ]
        remediations = []
        for item in data["remediations"]:
            action = item["action"]
            lowered = action.lower()
            inferred_write = any(marker in lowered for marker in _WRITE_MARKERS)
            inferred_cost = any(marker in lowered for marker in _COST_MARKERS)
            remediations.append(
                RemediationProposal(
                    action=action,
                    risk_level=item["risk_level"],
                    cost_impact=bool(item["cost_impact"]) or inferred_cost,
                    requires_write=bool(item["requires_write"]) or inferred_write,
                )
            )
        return IncidentAnalysis(
            incident_id=request.incident_id,
            service=request.service,
            impact_summary=data["impact_summary"],
            timeline=timeline,
            hypotheses=hypotheses,
            remediations=remediations,
            preventive_actions=list(data["preventive_actions"]),
            evidence_count=len(signals),
            cost_approval_required=any(item.cost_impact for item in remediations),
            raw=data,
        )

    @staticmethod
    def _known_refs(values: list[str], known: set[str]) -> list[str]:
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(
                "incident analysis referenced unknown evidence IDs: " + ", ".join(unknown)
            )
        return list(values)
