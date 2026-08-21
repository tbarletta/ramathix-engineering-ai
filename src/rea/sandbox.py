from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .domain import Decision
from .policy import CommandPolicy


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str


class PolicyViolation(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, policy: CommandPolicy, audit: AuditLog) -> None:
        self.policy = policy
        self.audit = audit

    def run(
        self,
        *,
        image: str,
        workspace: Path,
        argv: Sequence[str],
        network: bool = False,
    ) -> ExecutionResult:
        policy_result = self.policy.evaluate(argv)
        self.audit.write(
            "command.policy",
            actor="sandbox",
            data={
                "argv": list(argv),
                "decision": policy_result.decision,
                "rule": policy_result.rule_id,
            },
        )

        if policy_result.decision == Decision.DENY:
            raise PolicyViolation(policy_result.reason)
        if policy_result.decision in {Decision.ASK, Decision.COST_APPROVAL}:
            raise ApprovalRequired(policy_result.decision.value)

        workspace = workspace.resolve()
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
        ]
        if not network:
            docker_command.extend(["--network", "none"])
        docker_command.extend([image, *argv])

        completed = subprocess.run(
            docker_command,
            check=False,
            capture_output=True,
            text=True,
        )
        self.audit.write(
            "command.executed",
            actor="sandbox",
            data={"argv": list(argv), "returncode": completed.returncode},
        )
        return ExecutionResult(completed.returncode, completed.stdout, completed.stderr)

    @staticmethod
    def parse(command: str) -> list[str]:
        return shlex.split(command)
