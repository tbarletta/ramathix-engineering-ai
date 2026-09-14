from pathlib import Path

import pytest

from rea.audit import AuditLog
from rea.domain import Decision
from rea.execution import ExecutionApprovalRequired, GovernedLocalRunner
from rea.level6.coding import (
    FileMutation,
    MutationBoundaryError,
    SecretDetected,
    WorkspaceMutator,
    WorkspaceReader,
)
from rea.level6.workflow import Level6Workflow
from rea.level6.workspace import Worktree, WorkspaceBoundaryError, WorkspaceManager
from rea.policy import CommandPolicy
from rea.team.contracts import (
    DeveloperProposal,
    EngineeringBrief,
    FileChangeProposal,
    TechnicalPlan,
    WorkPackage,
)


def test_mutator_applies_only_planned_file(tmp_path: Path) -> None:
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutator = WorkspaceMutator(tmp_path, allowed_paths={"src/service.py"})
    mutator.apply(
        FileMutation(
            path="src/service.py",
            action="modify",
            content="VALUE = 2\n",
        )
    )
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_mutator_blocks_path_outside_plan(tmp_path: Path) -> None:
    mutator = WorkspaceMutator(tmp_path, allowed_paths={"src/service.py"})
    with pytest.raises(MutationBoundaryError):
        mutator.apply(
            FileMutation(
                path="../outside.py",
                action="create",
                content="SAFE = True\n",
            )
        )


def test_mutator_blocks_sensitive_path(tmp_path: Path) -> None:
    mutator = WorkspaceMutator(tmp_path, allowed_paths={".env"})
    with pytest.raises(MutationBoundaryError):
        mutator.apply(
            FileMutation(
                path=".env",
                action="create",
                content="TOKEN=placeholder\n",
            )
        )


def test_mutator_blocks_generated_secret(tmp_path: Path) -> None:
    mutator = WorkspaceMutator(tmp_path, allowed_paths={"src/config.py"})
    with pytest.raises(SecretDetected):
        mutator.apply(
            FileMutation(
                path="src/config.py",
                action="create",
                content='API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n',
            )
        )


def test_reader_redacts_secret_like_source(tmp_path: Path) -> None:
    target = tmp_path / "src" / "config.py"
    target.parent.mkdir()
    target.write_text(
        'token = "this-is-a-real-looking-token-value"\n',
        encoding="utf-8",
    )
    payload = WorkspaceReader(tmp_path).snapshot({"src/config.py"})
    assert "this-is-a-real-looking-token-value" not in payload["src/config.py"]
    assert "REDACTED_SECRET" in payload["src/config.py"]


def test_level6_requires_explicit_git_push_approval(tmp_path: Path) -> None:
    policy = CommandPolicy(
        default=Decision.ASK,
        rules=[
            {
                "id": "git-push",
                "decision": "ask",
                "prefixes": [["git", "push"]],
            }
        ],
    )
    workflow = object.__new__(Level6Workflow)
    workflow.policy = policy
    with pytest.raises(ExecutionApprovalRequired):
        workflow._require_publish_approval(set())
    workflow._require_publish_approval({"git-push"})


def test_allowed_paths_are_derived_from_approved_plan() -> None:
    package = WorkPackage(
        repository="tbarletta/example",
        issue_number=1,
        knowledge_repository="example",
        knowledge_root="/workspace/example",
        engineering_brief=EngineeringBrief(
            objective="Change API",
            business_context="Test",
        ),
        technical_plan=TechnicalPlan(
            architecture_summary="Keep current architecture",
            decision="approve",
            rationale="Safe",
            tasks=[],
        ),
        developer_proposal=DeveloperProposal(
            summary="Implementation",
            files=[
                FileChangeProposal(
                    path="src/service.py",
                    action="modify",
                    rationale="Required",
                )
            ],
        ),
    )
    assert Level6Workflow._allowed_paths(package) == {"src/service.py"}


def test_local_runner_never_auto_approves_cost(tmp_path: Path) -> None:
    policy = CommandPolicy(
        default=Decision.ASK,
        rules=[
            {
                "id": "cost-sensitive",
                "decision": "cost_approval",
                "prefixes": [["terraform", "apply"]],
            }
        ],
    )
    runner = GovernedLocalRunner(policy, AuditLog(tmp_path / "audit.jsonl"))
    with pytest.raises(ExecutionApprovalRequired) as exc:
        runner.run(
            ["terraform", "apply"],
            cwd=tmp_path,
            approved_rules={"cost-sensitive"},
        )
    assert exc.value.decision is Decision.COST_APPROVAL


def test_validation_cannot_change_unplanned_files(tmp_path: Path) -> None:
    approved = tmp_path / "src" / "service.py"
    unexpected = tmp_path / ".github" / "workflows" / "ci.yml"
    approved.parent.mkdir(parents=True)
    unexpected.parent.mkdir(parents=True)
    approved.write_text("VALUE = 1\n", encoding="utf-8")
    unexpected.write_text("name: changed\n", encoding="utf-8")

    class FakeRunner:
        def run(self, argv, **kwargs):
            if argv[:2] == ["git", "status"]:
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": " M .github/workflows/ci.yml\\0",
                        "stderr": "",
                    },
                )()
            raise AssertionError(argv)

    manager = WorkspaceManager(
        source_root=tmp_path,
        worktree_root=tmp_path / "worktrees",
        runner=FakeRunner(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    worktree = Worktree(tmp_path, "branch", "main")
    with pytest.raises(WorkspaceBoundaryError):
        manager.assert_only_allowed_changes(worktree, {"src/service.py"})
