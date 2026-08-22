from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from ..audit import AuditLog
from .agent import IncidentSREAgent
from .contracts import IncidentAnalysis, IncidentRequest, ProductionSignal
from .providers import ProductionSignalProvider
from .redaction import redact_value

_INCIDENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _sanitize_request(request: IncidentRequest) -> IncidentRequest:
    incident_id = request.incident_id.strip()
    if not _INCIDENT_ID.fullmatch(incident_id):
        raise ValueError(
            "incident_id must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_' or '-' (max 128 characters)"
        )
    payload = redact_value(asdict(request))
    payload["incident_id"] = incident_id
    return IncidentRequest(**payload)


class IncidentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self,
        request: IncidentRequest,
        signals: list[ProductionSignal],
        analysis: IncidentAnalysis,
    ) -> tuple[Path, Path]:
        safe_request = _sanitize_request(request)
        self.root.mkdir(parents=True, exist_ok=True)
        json_path = self.root / f"{safe_request.incident_id}.json"
        md_path = self.root / f"{safe_request.incident_id}.md"
        payload = redact_value(
            {
                "incident": asdict(safe_request),
                "signals": [item.to_dict() for item in signals],
                "analysis": analysis.to_dict(),
            }
        )
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(self._postmortem(safe_request, analysis), encoding="utf-8")
        return json_path, md_path

    @staticmethod
    def _postmortem(request: IncidentRequest, analysis: IncidentAnalysis) -> str:
        timeline = "\n".join(
            f"- {item.timestamp}: {item.event} ({', '.join(item.evidence_ids)})"
            for item in analysis.timeline
        ) or "- No timeline events."
        hypotheses = "\n".join(
            f"- [{item.confidence}] {item.statement} ({', '.join(item.evidence_ids)})"
            for item in analysis.hypotheses
        ) or "- No supported hypothesis."
        remediations = "\n".join(
            f"- {item.action} | risk={item.risk_level} | "
            f"write={item.requires_write} | cost={item.cost_impact}"
            for item in analysis.remediations
        ) or "- No remediation proposed."
        prevention = "\n".join(
            f"- {item}" for item in analysis.preventive_actions
        ) or "- No preventive action proposed."
        return (
            f"# Postmortem — {request.incident_id}\n\n"
            f"## Incident\n{request.title}\n\n"
            f"## Service\n{request.service}\n\n"
            f"## Impact\n{analysis.impact_summary}\n\n"
            f"## Timeline\n{timeline}\n\n"
            f"## Root-cause hypotheses\n{hypotheses}\n\n"
            f"## Proposed remediation\n{remediations}\n\n"
            f"## Preventive actions\n{prevention}\n"
        )


class IncidentWorkflow:
    def __init__(
        self,
        *,
        provider: ProductionSignalProvider,
        agent: IncidentSREAgent,
        store: IncidentStore,
        audit: AuditLog,
    ) -> None:
        self.provider = provider
        self.agent = agent
        self.store = store
        self.audit = audit

    def inspect(self, request: IncidentRequest) -> dict:
        safe_request = _sanitize_request(request)
        signals = self._collect(request, audit_request=safe_request)
        by_kind: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for item in signals:
            by_kind[item.kind.value] = by_kind.get(item.kind.value, 0) + 1
            by_severity[item.severity] = by_severity.get(item.severity, 0) + 1
        return {
            "incident_id": safe_request.incident_id,
            "service": safe_request.service,
            "signals": len(signals),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "latest": [item.to_dict() for item in signals[-20:]],
        }

    def analyze(self, request: IncidentRequest) -> tuple[IncidentAnalysis, Path, Path]:
        safe_request = _sanitize_request(request)
        signals = self._collect(request, audit_request=safe_request)
        analysis = self.agent.analyze(safe_request, signals)
        json_path, md_path = self.store.save(safe_request, signals, analysis)
        self.audit.write(
            "incident.analyzed",
            actor="incident_sre",
            data={
                "incident": safe_request.incident_id,
                "service": safe_request.service,
                "evidence_count": len(signals),
                "hypotheses": len(analysis.hypotheses),
                "cost_approval_required": analysis.cost_approval_required,
                "artifact": str(json_path),
            },
        )
        return analysis, json_path, md_path

    def _collect(
        self,
        request: IncidentRequest,
        *,
        audit_request: IncidentRequest | None = None,
    ) -> list[ProductionSignal]:
        raw = self.provider.collect(request)
        signals = []
        for item in raw:
            payload = redact_value(item.to_dict())
            signals.append(
                ProductionSignal(
                    evidence_id=payload["evidence_id"],
                    timestamp=payload["timestamp"],
                    kind=item.kind,
                    source=payload["source"],
                    service=payload["service"],
                    summary=payload["summary"],
                    severity=payload["severity"],
                    attributes=dict(payload["attributes"]),
                )
            )
        signals.sort(key=lambda item: (item.timestamp, item.evidence_id))
        safe_request = audit_request or _sanitize_request(request)
        self.audit.write(
            "incident.evidence_collected",
            actor="production_read",
            data={
                "incident": safe_request.incident_id,
                "service": safe_request.service,
                "count": len(signals),
                "evidence_ids": [item.evidence_id for item in signals],
            },
        )
        return signals
