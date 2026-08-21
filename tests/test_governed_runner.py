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
