from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentCommands:
    shadow: tuple[str, ...]
    canary: tuple[str, ...]
    health: tuple[str, ...]
    promote: tuple[str, ...]
    rollback: tuple[str, ...]
    verify_rollback: tuple[str, ...]

    @classmethod
    def from_json(cls, path: Path) -> DeploymentCommands:
        data = json.loads(path.read_text("utf-8"))
        return cls(**{key: tuple(value) for key, value in data.items()})


class CommandDeploymentAdapter:
    """Runs explicitly configured argv templates; shell expansion is never enabled."""

    def __init__(self, commands: DeploymentCommands, cwd: Path) -> None:
        self.commands = commands
        self.cwd = cwd

    def deploy_shadow(self, candidate_ref: str) -> str:
        return self._run(self.commands.shadow, candidate=candidate_ref)

    def deploy_canary(self, candidate_ref: str, percent: int) -> str:
        return self._run(self.commands.canary, candidate=candidate_ref, percent=str(percent))

    def metrics_healthy(self, deployment_id: str) -> bool:
        return self._execute(self.commands.health, deployment=deployment_id).returncode == 0

    def promote(self, deployment_id: str) -> None:
        result = self._execute(self.commands.promote, deployment=deployment_id)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "promotion command failed")

    def rollback(self, deployment_id: str) -> None:
        result = self._execute(self.commands.rollback, deployment=deployment_id)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "rollback command failed")
        verification = self._execute(self.commands.verify_rollback, deployment=deployment_id)
        if verification.returncode != 0:
            raise RuntimeError("rollback command completed but verification failed")

    def _run(self, command: tuple[str, ...], **values: str) -> str:
        result = self._execute(command, **values)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "deployment command failed")
        value = result.stdout.strip().splitlines()
        if not value:
            raise RuntimeError("deployment command did not return an identifier")
        return value[-1]

    def _execute(self, command: tuple[str, ...], **values: str) -> subprocess.CompletedProcess:
        argv = [part.format_map(values) for part in command]
        return subprocess.run(
            argv,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
