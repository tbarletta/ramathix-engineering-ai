from pathlib import Path
from types import SimpleNamespace

import pytest

from rea.audit import AuditLog
from rea.production.agent import IncidentSREAgent
from rea.production.contracts import (
    IncidentAnalysis,
    IncidentRequest,
    ProductionSignal,
    SignalKind,
)
from rea.production.workflow import IncidentStore, IncidentWorkflow


class FakeRouter:
    def resolve(self, role: str):
        assert role == "devops_sre"
        return SimpleNamespace(model="fake-sre")


def request() -> IncidentRequest:
    return IncidentRequest(
        incident_id="INC-SAFE",
        title="Latency incident",
        service="payments",
        description="Latency increased",
    )


def evidence() -> list[ProductionSignal]:
    return [
        ProductionSignal(
            evidence_id="metric-1",
            timestamp="2026-08-21T18:00:00Z",
            kind=SignalKind.METRIC,
            source="prometheus",
            service="payments",
            summary="p95 latency increased",
            severity="warning",
        )
    ]


class ForgottenCostModel:
    def chat_json(self, **kwargs):
        return {
            "impact_summary": "Latency degradation",
            "timeline": [],
            "hypotheses": [
                {
                    "statement": "Capacity may be insufficient",
                    "confidence": "medium",
                    "evidence_ids": ["metric-1"],
                }
            ],
            "remediations": [
                {
                    "action": "Scale service to 4 replicas",
                    "risk_level": "medium",
                    "cost_impact": False,
                    "requires_write": False,
                }
            ],
            "preventive_actions": [],
        }


def test_cost_and_write_are_inferred_when_model_forgets() -> None:
    analysis = IncidentSREAgent(FakeRouter(), ForgottenCostModel()).analyze(
        request(),
        evidence(),
    )
    remediation = analysis.remediations[0]
    assert remediation.requires_write is True
    assert remediation.cost_impact is True
    assert analysis.cost_approval_required is True


class SecretOutputModel:
    def chat_json(self, **kwargs):
        return {
            "impact_summary": 'token="this-is-a-sensitive-output-token"',
            "timeline": [],
            "hypotheses": [],
            "remediations": [],
            "preventive_actions": [
                'Rotate api_key="this-is-another-sensitive-output-value"'
            ],
        }


def test_model_output_is_redacted_before_becoming_analysis() -> None:
    analysis = IncidentSREAgent(FakeRouter(), SecretOutputModel()).analyze(
        request(),
        evidence(),
    )
    assert "sensitive-output-token" not in analysis.impact_summary
    assert "sensitive-output-value" not in analysis.preventive_actions[0]
    assert "REDACTED_SECRET" in analysis.impact_summary


def test_store_rejects_path_traversal_incident_id(tmp_path: Path) -> None:
    unsafe = IncidentRequest(
        incident_id="../../escape",
        title="Unsafe",
        service="payments",
        description="Unsafe identifier",
    )
    analysis = IncidentAnalysis(
        incident_id=unsafe.incident_id,
        service=unsafe.service,
        impact_summary="None",
    )
    with pytest.raises(ValueError, match="incident_id"):
        IncidentStore(tmp_path / "incidents").save(unsafe, [], analysis)
    assert not (tmp_path / "escape.json").exists()


class StaticProvider:
    def collect(self, incident):
        return evidence()


class RequestRedactionModel:
    def chat_json(self, **kwargs):
        user = kwargs["user"]
        assert "sensitive-request-token" not in user
        assert "REDACTED_SECRET" in user
        return {
            "impact_summary": "No confirmed impact",
            "timeline": [],
            "hypotheses": [],
            "remediations": [],
            "preventive_actions": [],
        }


def secret_request() -> IncidentRequest:
    return IncidentRequest(
        incident_id="INC-REQUEST-SECRET",
        title="Credential appeared in incident input",
        service="payments",
        description='token="this-is-a-sensitive-request-token"',
    )


def test_agent_redacts_request_without_workflow() -> None:
    IncidentSREAgent(FakeRouter(), RequestRedactionModel()).analyze(
        secret_request(),
        evidence(),
    )


def test_request_secrets_are_redacted_before_prompt_and_artifacts(tmp_path: Path) -> None:
    workflow = IncidentWorkflow(
        provider=StaticProvider(),
        agent=IncidentSREAgent(FakeRouter(), RequestRedactionModel()),
        store=IncidentStore(tmp_path / "incidents"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    _, json_path, md_path = workflow.analyze(secret_request())
    assert "sensitive-request-token" not in json_path.read_text(encoding="utf-8")
    assert "sensitive-request-token" not in md_path.read_text(encoding="utf-8")
    assert "sensitive-request-token" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
