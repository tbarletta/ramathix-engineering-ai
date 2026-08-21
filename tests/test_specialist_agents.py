from pathlib import Path

import pytest

from rea.audit import AuditLog
from rea.level6.coding import MutationBoundaryError, SecretDetected
from rea.models import ModelRouter
from rea.specialists.agents import QATestingGateAgent, SpecializedDeveloperAgent
from rea.specialists.integration import RoutedSeniorDeveloperExecutionAgent
from rea.specialists.profiles import SpecialistRole
from rea.specialists.router import AgentAssignment, RoutingError
from rea.team.context import TeamKnowledgeContext


class FakeModel:
    def __init__(self, response):
        self.response = response

    def chat_json(self, **kwargs):
        return self.response


def context() -> TeamKnowledgeContext:
    return TeamKnowledgeContext(
        repository_name="example",
        repository_root="/workspace/example",
        inventory={},
        compact={
            "repository": "example",
            "facts": [],
            "languages": {"Python": 1},
            "manifests": ["pyproject.toml"],
        },
    )


def router() -> ModelRouter:
    return ModelRouter(
        models={
            "coder": {
                "provider": "ollama",
                "model": "fake-coder",
                "context_window": 8192,
            }
        },
        routes={"senior_backend": "coder", "qa_testing": "coder"},
    )


def test_specialist_cannot_mutate_outside_assignment(tmp_path: Path) -> None:
    agent = SpecializedDeveloperAgent(
        router=router(),
        model=FakeModel(
            {
                "summary": "bad scope",
                "changes": [
                    {
                        "path": "src/other.py",
                        "action": "create",
                        "content": "VALUE = 1\n",
                    }
                ],
                "commands": [],
                "cost_impact": False,
            }
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    assignment = AgentAssignment(
        task_id="api",
        role=SpecialistRole.BACKEND,
        repository="tbarletta/example",
        files=("src/api.py",),
        dependencies=(),
        reason="explicit",
        confidence="explicit",
    )
    with pytest.raises(RoutingError):
        agent.implement(
            assignment=assignment,
            task={"id": "api"},
            work_package={},
            context=context(),
            source_files={"src/api.py": "VALUE = 0\n"},
            feedback=[],
            upstream_changes=[],
        )


def test_routed_executor_converts_scope_violation_to_level6_block(tmp_path: Path) -> None:
    routed = RoutedSeniorDeveloperExecutionAgent(
        router=router(),
        model=FakeModel(
            {
                "summary": "bad scope",
                "changes": [
                    {
                        "path": "src/other.py",
                        "action": "create",
                        "content": "VALUE = 1\n",
                    }
                ],
                "commands": [],
                "cost_impact": False,
            }
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    work_package = {
        "repository": "tbarletta/example",
        "technical_plan": {
            "tasks": [
                {
                    "id": "api",
                    "owner_role": "senior_backend",
                    "files": ["src/api.py"],
                    "dependencies": [],
                }
            ]
        },
    }
    with pytest.raises(MutationBoundaryError, match="specialist execution blocked"):
        routed.implement(
            work_package=work_package,
            context=context(),
            source_files={"src/api.py": "VALUE = 0\n"},
            feedback=[],
        )


def test_routed_executor_rejects_generated_secret_before_handoff(tmp_path: Path) -> None:
    routed = RoutedSeniorDeveloperExecutionAgent(
        router=router(),
        model=FakeModel(
            {
                "summary": "secret",
                "changes": [
                    {
                        "path": "src/api.py",
                        "action": "modify",
                        "content": 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n',
                    }
                ],
                "commands": [],
                "cost_impact": False,
            }
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    work_package = {
        "repository": "tbarletta/example",
        "technical_plan": {
            "tasks": [
                {
                    "id": "api",
                    "owner_role": "senior_backend",
                    "files": ["src/api.py"],
                    "dependencies": [],
                }
            ]
        },
    }
    with pytest.raises(SecretDetected):
        routed.implement(
            work_package=work_package,
            context=context(),
            source_files={"src/api.py": "VALUE = 0\n"},
            feedback=[],
        )


def test_qa_agent_can_block_for_missing_tests(tmp_path: Path) -> None:
    qa = QATestingGateAgent(
        router=router(),
        model=FakeModel(
            {
                "decision": "request_changes",
                "summary": "Critical path is not covered",
                "findings": ["Failure path is untested"],
                "missing_tests": ["Add payment retry regression test"],
                "cost_impact": False,
            }
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    result = qa.evaluate(
        work_package={},
        context=context(),
        assignments=[],
        diff="+def retry(): pass",
        validation=[{"command": "pytest", "returncode": 0}],
    )
    assert result.decision == "request_changes"
    assert "payment retry" in result.missing_tests[0]
