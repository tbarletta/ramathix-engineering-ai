from types import SimpleNamespace

from rea.production.agent import IncidentSREAgent
from rea.production.contracts import IncidentRequest, ProductionSignal, SignalKind


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
