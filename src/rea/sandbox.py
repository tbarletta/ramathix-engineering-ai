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
    def __init__(
        self,
        decision: Decision,
        *,
        rule_id: str | None,
        argv: Sequence[str],
    ) -> None:
        super().__init__(decision.value)
        self.decision = decision
        self.rule_id = rule_id
        self.argv = tuple(argv)


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
        approved_rules: set[str] | None = None,
        timeout: int = 900,
    ) -> ExecutionResult:
        approved_rules = approved_rules or set()
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
        if policy_result.decision == Decision.COST_APPROVAL:
            raise ApprovalRequired(
                policy_result.decision,
                rule_id=policy_result.rule_id,
                argv=argv,
            )
        if (
            policy_result.decision == Decision.ASK
            and policy_result.rule_id not in approved_rules
        ):
            raise ApprovalRequired(
                policy_result.decision,
                rule_id=policy_result.rule_id,
                argv=argv,
            )

        workspace = workspace.resolve()
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,size=512m",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
        ]
        git_metadata = workspace / ".git"
        if git_metadata.exists():
            docker_command.extend(
                [
                    "--mount",
                    (
                        f"type=bind,src={git_metadata},dst=/workspace/.git,"
                        "readonly"
                    ),
                ]
            )
        if not network:
            docker_command.extend(["--network", "none"])
        docker_command.extend([image, *argv])

        completed = subprocess.run(
            docker_command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
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
