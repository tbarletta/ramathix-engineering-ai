from pathlib import Path

import pytest

from rea.audit import AuditLog
from rea.domain import Decision
from rea.execution import GovernedLocalRunner
from rea.policy import CommandPolicy


def test_runner_refuses_commands_not_allowed_by_policy(tmp_path: Path) -> None:
    policy = CommandPolicy(default=Decision.ASK, rules=[])
    runner = GovernedLocalRunner(policy, AuditLog(tmp_path / "audit.jsonl"))
    with pytest.raises(PermissionError):
        runner.run(["git", "log"], cwd=tmp_path)


def test_runner_executes_explicitly_allowed_read_command(tmp_path: Path) -> None:
    policy = CommandPolicy(
        default=Decision.ASK,
        rules=[{"id": "git-read", "decision": "allow", "prefixes": [["git", "status"]]}],
    )
    runner = GovernedLocalRunner(policy, AuditLog(tmp_path / "audit.jsonl"))
    result = runner.run(["git", "status"], cwd=tmp_path)
    assert result.returncode != 0
    assert result.argv == ("git", "status")
