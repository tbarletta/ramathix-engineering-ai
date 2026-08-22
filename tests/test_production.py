import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from rea.audit import AuditLog
from rea.production.agent import IncidentSREAgent
from rea.production.cli import app
from rea.production.contracts import IncidentRequest, ProductionSignal, SignalKind
from rea.production.policy import (
    ProductionCapability,
    ProductionReadPolicy,
    ProductionWriteDenied,
)
from rea.production.providers import GitHistorySignalProvider, JsonFileSignalProvider
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
    policy.authorize(ProductionCapability.SOURCE_READ)
    with pytest.raises(ProductionWriteDenied):
        policy.authorize(ProductionCapability.DATABASE_READ, write=True)


def test_status_command_preserves_documented_name() -> None:
    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Ramathix Engineering AI V1.0.0" in result.stdout


def test_bare_cli_starts_a_conversational_session() -> None:
    result = CliRunner().invoke(app, input="/exit\n")

    assert result.exit_code == 0
    assert "local conversational session" in result.stdout
    assert "Session closed." in result.stdout


def test_chat_command_starts_a_conversational_session() -> None:
    result = CliRunner().invoke(app, ["chat"], input="/exit\n")

    assert result.exit_code == 0
    assert "local conversational session" in result.stdout


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


class FakeGitRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if argv[:2] == ["git", "log"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "abcdef1234567890\x1f2026-08-21T17:59:00+00:00\x1f"
                    "fix provider timeout\n"
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="src/payments/provider.py\ntests/test_provider.py\n",
            stderr="",
        )


def test_git_provider_adds_governed_change_evidence(tmp_path: Path) -> None:
    runner = FakeGitRunner()
    signals = GitHistorySignalProvider(
        tmp_path,
        policy=ProductionReadPolicy(),
        runner=runner,
    ).collect(request())
    assert len(signals) == 1
    assert signals[0].kind is SignalKind.GIT_CHANGE
    assert signals[0].attributes["sha"] == "abcdef1234567890"
    assert signals[0].attributes["files"] == [
        "src/payments/provider.py",
        "tests/test_provider.py",
    ]
    assert runner.calls[0][:2] == ("git", "log")
    assert runner.calls[1][:2] == ("git", "show")


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


class UnknownEvidenceModel(FakeModel):
    def chat_json(self, **kwargs):
        data = super().chat_json(**kwargs)
        data["hypotheses"][0]["evidence_ids"] = ["invented-evidence"]
        return data


def test_incident_agent_rejects_invented_evidence() -> None:
    with pytest.raises(ValueError, match="unknown evidence IDs"):
        IncidentSREAgent(FakeRouter(), UnknownEvidenceModel()).analyze(
            request(),
            evidence(),
        )


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
