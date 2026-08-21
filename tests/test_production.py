import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rea.audit import AuditLog
from rea.production.agent import IncidentSREAgent
from rea.production.contracts import IncidentRequest, ProductionSignal, SignalKind
from rea.production.policy import (
    ProductionCapability,
    ProductionReadPolicy,
    ProductionWriteDenied,
)
from rea.production.providers import JsonFileSignalProvider
from rea.production.redaction import redact_text
from rea.production.workflow import IncidentStore, IncidentWorkflow


def request() -> IncidentRequest:
    return IncidentRequest(
        incident_id="INC-001",
        title="API errors",
        service="payments",
        description="Elevated 5xx",
    )


def test_production_policy_never_allows_write() -> None:
    policy = ProductionReadPolicy()
    policy.authorize(ProductionCapability.LOGS_READ)
    with pytest.raises(ProductionWriteDenied):
        policy.authorize(ProductionCapability.DATABASE_READ, write=True)


def test_json_provider_filters_service(tmp_path: Path) -> None:
    path = tmp_path / "signals.json"
    path.write_text(
        json.dumps(
            [
                {
                    "evidence_id": "log-1",
                    "timestamp": "2026-08-21T18:00:00Z",
                    "kind": "log",
                    "service": "payments",
                    "summary": "500 from provider",
                },
                {
                    "evidence_id": "log-2",
                    "timestamp": "2026-08-21T18:00:01Z",
                    "kind": "log",
                    "service": "other",
                    "summary": "ignore",
                },
            ]
        ),
        encoding="utf-8",
    )
    signals = JsonFileSignalProvider(
        path,
        policy=ProductionReadPolicy(),
    ).collect(request())
    assert [item.evidence_id for item in signals] == ["log-1"]


def test_redaction_removes_credentials() -> None:
    value = 'token="this-is-a-sensitive-token-value"'
    assert "sensitive-token-value" not in redact_text(value)


class FakeRouter:
    def resolve(self, role: str):
        assert role == "devops_sre"
        return SimpleNamespace(model="fake-sre")


class FakeModel:
    def chat_json(self, **kwargs):
        return {
            "impact_summary": "Payment requests failed",
            "timeline": [
                {
                    "timestamp": "2026-08-21T18:00:00Z",
                    "event": "Deploy followed by 5xx",
                    "evidence_ids": ["deploy-1", "log-1"],
                }
            ],
            "hypotheses": [
                {
                    "statement": "Deploy introduced provider timeout regression",
                    "confidence": "high",
                    "evidence_ids": ["deploy-1", "log-1"],
                }
            ],
            "remediations": [
                {
                    "action": "Restart the payment worker",
                    "risk_level": "medium",
                    "cost_impact": False,
                    "requires_write": False,
                }
            ],
            "preventive_actions": ["Add provider timeout regression test"],
        }


def evidence() -> list[ProductionSignal]:
    return [
        ProductionSignal(
            evidence_id="deploy-1",
            timestamp="2026-08-21T17:59:00Z",
            kind=SignalKind.DEPLOYMENT,
            source="deploy",
            service="payments",
            summary="release 42",
        ),
        ProductionSignal(
            evidence_id="log-1",
            timestamp="2026-08-21T18:00:00Z",
            kind=SignalKind.LOG,
            source="app",
            service="payments",
            summary="provider timeout",
            severity="error",
        ),
    ]


def test_incident_agent_marks_write_remediation_deterministically() -> None:
    analysis = IncidentSREAgent(FakeRouter(), FakeModel()).analyze(
        request(),
        evidence(),
    )
    assert analysis.hypotheses[0].confidence == "high"
    assert analysis.remediations[0].requires_write is True


class FakeProvider:
    def collect(self, incident):
        return [
            ProductionSignal(
                evidence_id="log-secret",
                timestamp="2026-08-21T18:00:00Z",
                kind=SignalKind.LOG,
                source="app",
                service=incident.service,
                summary='password="super-sensitive-password"',
            )
        ]


class EvidenceModel:
    def chat_json(self, **kwargs):
        user = kwargs["user"]
        assert "super-sensitive-password" not in user
        return {
            "impact_summary": "Unknown",
            "timeline": [],
            "hypotheses": [],
            "remediations": [],
            "preventive_actions": [],
        }


def test_workflow_redacts_before_model_and_postmortem(tmp_path: Path) -> None:
    workflow = IncidentWorkflow(
        provider=FakeProvider(),
        agent=IncidentSREAgent(FakeRouter(), EvidenceModel()),
        store=IncidentStore(tmp_path / "incidents"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    _, json_path, md_path = workflow.analyze(request())
    assert "super-sensitive-password" not in json_path.read_text(encoding="utf-8")
    assert "super-sensitive-password" not in md_path.read_text(encoding="utf-8")
