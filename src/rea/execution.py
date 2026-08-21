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


class ExecutionDenied(RuntimeError):
    pass


class ExecutionApprovalRequired(RuntimeError):
    def __init__(
        self,
        *,
        decision: Decision,
        rule_id: str | None,
        argv: list[str],
        reason: str,
    ) -> None:
        super().__init__(f"{decision.value} required for {' '.join(argv)}")
        self.decision = decision
        self.rule_id = rule_id
        self.argv = tuple(argv)
        self.reason = reason


class GovernedLocalRunner:
    """Runs local commands only after the central command policy authorizes them."""

    def __init__(self, policy: CommandPolicy, audit: AuditLog) -> None:
        self.policy = policy
        self.audit = audit

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int = 30,
        approved_rules: set[str] | None = None,
        actor: str = "execution_engine",
    ) -> CommandResult:
        approved_rules = approved_rules or set()
        policy_result = self.policy.evaluate(argv)
        self.audit.write(
            "command.policy_checked",
            actor=actor,
            data={
                "argv": argv,
                "decision": policy_result.decision,
                "rule": policy_result.rule_id,
            },
        )

        if policy_result.decision is Decision.DENY:
            raise ExecutionDenied(policy_result.reason)
        if policy_result.decision is Decision.COST_APPROVAL:
            raise ExecutionApprovalRequired(
                decision=policy_result.decision,
                rule_id=policy_result.rule_id,
                argv=argv,
                reason=policy_result.reason,
            )
        if (
            policy_result.decision is Decision.ASK
            and policy_result.rule_id not in approved_rules
        ):
            raise ExecutionApprovalRequired(
                decision=policy_result.decision,
                rule_id=policy_result.rule_id,
                argv=argv,
                reason=policy_result.reason,
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
            actor=actor,
            data={"argv": argv, "returncode": completed.returncode},
        )
        return CommandResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
