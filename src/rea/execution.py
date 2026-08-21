from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .domain import Decision
from .policy import CommandPolicy


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class GovernedLocalRunner:
    """Runs non-LLM local commands only after the central command policy allows them."""

    def __init__(self, policy: CommandPolicy, audit: AuditLog) -> None:
        self.policy = policy
        self.audit = audit

    def run(self, argv: list[str], *, cwd: Path, timeout: int = 15) -> CommandResult:
        policy_result = self.policy.evaluate(argv)
        self.audit.write(
            "command.policy_checked",
            actor="knowledge_engine",
            data={"argv": argv, "decision": policy_result.decision, "rule": policy_result.rule_id},
        )
        if policy_result.decision is not Decision.ALLOW:
            raise PermissionError(
                f"command requires {policy_result.decision}: {' '.join(argv)}"
            )

        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        self.audit.write(
            "command.executed",
            actor="knowledge_engine",
            data={"argv": argv, "returncode": completed.returncode},
        )
        return CommandResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
