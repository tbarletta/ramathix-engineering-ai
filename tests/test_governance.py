from types import SimpleNamespace

import pytest

from rea.audit import AuditLog
from rea.governance import (
    AdvancedGovernanceWorkflow,
    GovernanceApprovalRequired,
    GovernanceBlocked,
    GovernanceDecision,
    GovernanceFinding,
    GovernanceRiskLevel,
    RiskEngine,
    SpecialistAssessment,
)
from rea.governance.agents import SecurityAgent
from rea.team import CostApprovalRequired
from rea.team.context import TeamKnowledgeContext


class FakeRouter:
    def resolve(self, role: str):
        return SimpleNamespace(model=f"fake-{role}")


class SafeModel:
    def chat_json(self, **kwargs):
        return {
            "summary": "No additional specialist escalation.",
            "findings": [],
            "cost_impact": False,
            "production_write": False,
            "requires_human": False,
        }


class SecretEchoModel:
    def chat_json(self, **kwargs):
        user = kwargs["user"]
        assert "sensitive-input-token" not in user
        assert "REDACTED_SECRET" in user
        return {
            "summary": 'token="this-is-a-sensitive-output-token"',
            "findings": [],
            "cost_impact": False,
            "production_write": False,
            "requires_human": False,
        }


def package(*, risk: str = "low", text: str = "small internal refactor") -> dict:
    return {
        "repository": "example",
        "issue_number": 1,
        "knowledge_repository": "example",
        "knowledge_root": "/workspace/example",
        "engineering_brief": {
            "objective": text,
            "risk_level": risk,
            "cost_impact": False,
        },
        "technical_plan": {
            "architecture_summary": text,
            "risks": [],
            "cost_impact": False,
            "tasks": [],
        },
        "developer_proposal": {
            "summary": text,
            "commands": [],
            "cost_impact": False,
        },
    }


def context() -> TeamKnowledgeContext:
    return TeamKnowledgeContext(
        repository_name="example",
        repository_root="/workspace/example",
        inventory={},
        compact={"facts": [], "languages": {"Python": 1}},
    )


def test_low_risk_is_autonomous() -> None:
    assessment = RiskEngine().assess(package(), [])
    assert assessment.risk_level is GovernanceRiskLevel.LOW
    assert assessment.decision is GovernanceDecision.AUTONOMOUS


def test_security_sensitive_change_escalates_declared_medium() -> None:
    assessment = RiskEngine().assess(
        package(risk="medium", text="Change JWT authentication permissions"),
        [],
    )
    assert assessment.risk_level in {
        GovernanceRiskLevel.HIGH,
        GovernanceRiskLevel.CRITICAL,
    }
    assert assessment.decision in {
        GovernanceDecision.TECH_LEAD_APPROVAL,
        GovernanceDecision.HUMAN_APPROVAL,
    }


def test_specialist_can_raise_but_not_lower_risk() -> None:
    specialist = SpecialistAssessment(
        role="security",
        summary="Critical authorization issue.",
        findings=[
            GovernanceFinding(
                severity=GovernanceRiskLevel.CRITICAL,
                category="authorization",
                message="Privilege boundary may be bypassed.",
            )
        ],
    )
    assessment = RiskEngine().assess(package(), [specialist])
    assert assessment.risk_level is GovernanceRiskLevel.CRITICAL
    assert assessment.decision is GovernanceDecision.HUMAN_APPROVAL


def test_governance_agent_redacts_prompt_and_output() -> None:
    assessment = SecurityAgent(FakeRouter(), SecretEchoModel()).assess(
        work_package=package(text='token="this-is-a-sensitive-input-token"'),
        context=context(),
    )
    assert "sensitive-output-token" not in assessment.summary
    assert "REDACTED_SECRET" in assessment.summary


def test_production_write_is_blocked() -> None:
    assessment = RiskEngine().assess(
        package(risk="low", text="terraform apply to production"),
        [],
    )
    assert assessment.production_write is True
    assert assessment.decision is GovernanceDecision.BLOCKED


def test_production_reference_without_state_change_is_not_blocked() -> None:
    assessment = RiskEngine().assess(
        package(text="Production deployment is out of scope; change documentation only."),
        [],
    )
    assert assessment.production_write is False
    assert assessment.decision is not GovernanceDecision.BLOCKED


def test_terraform_apply_to_staging_is_cost_gate_not_production_write() -> None:
    assessment = RiskEngine().assess(
        package(text="terraform apply to staging infrastructure"),
        [],
    )
    assert assessment.production_write is False
    assert assessment.cost_impact is True
    assert assessment.decision is GovernanceDecision.COST_APPROVAL


def test_cost_gate_cannot_be_bypassed_by_human_risk_approval(tmp_path) -> None:
    workflow = AdvancedGovernanceWorkflow(
        router=FakeRouter(),
        model=SafeModel(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(CostApprovalRequired):
        workflow.enforce(
            package(text="increase replicas and provision additional storage"),
            context(),
            approvals={"human", "tech-lead"},
        )


def test_high_risk_requires_explicit_tech_lead_approval(tmp_path) -> None:
    workflow = AdvancedGovernanceWorkflow(
        router=FakeRouter(),
        model=SafeModel(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(GovernanceApprovalRequired) as exc:
        workflow.enforce(package(risk="high"), context())
    assert exc.value.required == "tech-lead"
    assert exc.value.rule_id == "governance-tech-lead"

    assessment = workflow.enforce(
        package(risk="high"),
        context(),
        approvals={"tech-lead"},
    )
    assert assessment.decision is GovernanceDecision.TECH_LEAD_APPROVAL


def test_critical_risk_requires_explicit_human_approval(tmp_path) -> None:
    workflow = AdvancedGovernanceWorkflow(
        router=FakeRouter(),
        model=SafeModel(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(GovernanceApprovalRequired) as exc:
        workflow.enforce(package(risk="critical"), context(), approvals={"tech-lead"})
    assert exc.value.required == "human"
    assert exc.value.rule_id == "governance-human"


def test_production_write_cannot_be_approved_by_governance_flag(tmp_path) -> None:
    workflow = AdvancedGovernanceWorkflow(
        router=FakeRouter(),
        model=SafeModel(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(GovernanceBlocked):
        workflow.enforce(
            package(text="restart production and deploy to production"),
            context(),
            approvals={"human", "tech-lead"},
        )
