import json
from pathlib import Path

import pytest

from rea.audit import AuditLog
from rea.domain import Decision, IssueWorkUnit
from rea.knowledge.store import JsonKnowledgeStore
from rea.models import ModelRouter
from rea.policy import CommandPolicy
from rea.team.agents import CostApprovalRequired
from rea.team.context import KnowledgeContextBuilder
from rea.team.workflow import FirstTeamWorkflow, WorkPackageStore


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, *, model, system, user, schema):
        self.calls.append((model, system, json.loads(user), schema))
        return self.responses.pop(0)


def write_inventory(root: Path):
    root.mkdir(parents=True)
    inv = {
        "name": "social-media",
        "root": "/workspace/social-media",
        "languages": {"TypeScript": 40},
        "manifests": ["package.json"],
        "facts": [
            {
                "category": "architecture",
                "name": "maybe",
                "value": "inferred",
                "confidence": "inferred_low",
                "evidence": {"source": "x", "kind": "inference"},
                "metadata": {},
            },
            {
                "category": "framework",
                "name": "NestJS",
                "value": "NestJS",
                "confidence": "confirmed",
                "evidence": {"source": "package.json", "kind": "manifest"},
                "metadata": {},
            },
        ],
        "dependencies": [],
        "symbols": [],
        "git": {},
        "warnings": [],
    }
    (root / "social-media.json").write_text(json.dumps(inv), encoding="utf-8")
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "name": "social-media",
                        "root": "/workspace/social-media",
                        "inventory": "social-media.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def router():
    return ModelRouter(
        models={
            "reasoner": {"provider": "ollama", "model": "reasoner"},
            "coder": {"provider": "ollama", "model": "coder"},
        },
        routes={
            "engineering_manager": "reasoner",
            "tech_lead": "reasoner",
            "senior_developer": "coder",
            "code_review": "reasoner",
        },
    )


def responses():
    return [
        {
            "objective": "Implement scheduling",
            "business_context": "Publishing",
            "acceptance_criteria": ["scheduled posts publish"],
            "constraints": ["respect architecture"],
            "impacted_repositories": ["social-media"],
            "risk_level": "medium",
            "cost_impact": False,
        },
        {
            "architecture_summary": "Use existing modules",
            "decision": "approve",
            "rationale": "Fits architecture",
            "tasks": [
                {
                    "id": "T1",
                    "owner_role": "senior_backend",
                    "description": "Add service",
                    "files": ["apps/api/src/x.ts"],
                    "dependencies": [],
                    "acceptance_criteria": ["works"],
                    "tests": ["unit test"],
                }
            ],
            "risks": ["queue retries"],
            "rollback_strategy": ["revert"],
            "cost_impact": False,
        },
        {
            "summary": "Implement service and tests",
            "files": [
                {
                    "path": "apps/api/src/x.ts",
                    "action": "modify",
                    "rationale": "add scheduling",
                }
            ],
            "implementation_steps": ["change service"],
            "tests": ["npm test"],
            "assumptions": [],
            "commands": ["npm test", "git push origin branch"],
            "cost_impact": False,
        },
    ]


def make_workflow(tmp_path, model):
    knowledge = tmp_path / "knowledge"
    write_inventory(knowledge)
    policy = CommandPolicy(
        default=Decision.ASK,
        rules=[
            {"id": "tests", "decision": "allow", "prefixes": [["npm", "test"]]},
            {"id": "push", "decision": "ask", "prefixes": [["git", "push"]]},
        ],
    )
    return FirstTeamWorkflow(
        router=router(),
        model=model,
        knowledge_store=JsonKnowledgeStore(knowledge),
        work_store=WorkPackageStore(tmp_path / "work"),
        policy=policy,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )


def test_context_prioritizes_confirmed_knowledge(tmp_path):
    knowledge = tmp_path / "knowledge"
    write_inventory(knowledge)
    context = KnowledgeContextBuilder(JsonKnowledgeStore(knowledge)).build("social-media")
    assert context.compact["facts"][0]["confidence"] == "confirmed"
    assert context.compact["facts"][1]["confidence"] == "inferred_low"


def test_first_team_plan_and_policy_classification(tmp_path):
    model = FakeModel(responses())
    workflow = make_workflow(tmp_path, model)
    issue = IssueWorkUnit(
        repository="tbarletta/social-media",
        number=42,
        title="Scheduling",
        body="Implement scheduling",
        url="https://example.test/42",
    )
    package, path = workflow.plan(issue, knowledge_repository="social-media")
    assert path.exists()
    assert package.technical_plan.decision == "approve"
    assert [x["decision"] for x in package.command_policy_results] == ["allow", "ask"]
    assert [call[0] for call in model.calls] == ["reasoner", "reasoner", "coder"]


def test_cost_gate_stops_after_engineering_manager(tmp_path):
    first = responses()[0]
    first["cost_impact"] = True
    model = FakeModel([first])
    workflow = make_workflow(tmp_path, model)
    issue = IssueWorkUnit("tbarletta/social-media", 7, "Scale VM", "Scale", "x")
    with pytest.raises(CostApprovalRequired) as exc:
        workflow.plan(issue, knowledge_repository="social-media")
    assert exc.value.stage == "engineering_manager"
    assert len(model.calls) == 1


def test_code_review_can_request_changes(tmp_path):
    model = FakeModel(
        responses()
        + [
            {
                "decision": "request_changes",
                "summary": "Missing security coverage",
                "findings": [
                    {
                        "severity": "high",
                        "category": "security",
                        "message": "Validate authorization before scheduling.",
                    }
                ],
                "missing_tests": ["authorization test"],
                "risk_notes": ["privilege escalation"],
                "cost_impact": False,
            }
        ]
    )
    workflow = make_workflow(tmp_path, model)
    issue = IssueWorkUnit(
        repository="tbarletta/social-media",
        number=43,
        title="Scheduling",
        body="Implement scheduling",
        url="https://example.test/43",
    )
    _, plan_path = workflow.plan(issue, knowledge_repository="social-media")
    review, review_path = workflow.review(plan_path)
    assert review.decision == "request_changes"
    assert review.findings[0].category == "security"
    assert review_path.exists()
    assert len(model.calls) == 4


def test_cost_sensitive_proposed_command_triggers_human_gate(tmp_path):
    model_responses = responses()
    model_responses[2]["commands"] = ["terraform apply"]
    model = FakeModel(model_responses)

    knowledge = tmp_path / "knowledge"
    write_inventory(knowledge)
    policy = CommandPolicy(
        default=Decision.ASK,
        rules=[
            {
                "id": "terraform-cost",
                "decision": "cost_approval",
                "prefixes": [["terraform", "apply"]],
            }
        ],
    )
    workflow = FirstTeamWorkflow(
        router=router(),
        model=model,
        knowledge_store=JsonKnowledgeStore(knowledge),
        work_store=WorkPackageStore(tmp_path / "work"),
        policy=policy,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    issue = IssueWorkUnit("tbarletta/social-media", 44, "Infra", "Apply infra", "x")
    with pytest.raises(CostApprovalRequired) as exc:
        workflow.plan(issue, knowledge_repository="social-media")
    assert exc.value.stage == "command_policy"
