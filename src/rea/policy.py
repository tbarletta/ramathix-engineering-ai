from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import yaml

from .domain import Decision, PolicyResult


_DESTRUCTIVE_GIT = {"clean", "reset"}
_MUTATING_GIT = {"checkout", "clone", "commit", "fetch", "merge", "pull", "push", "stash", "switch"}
_SHELL_EXECUTORS = {"bash", "cmd", "fish", "powershell", "pwsh", "sh", "zsh"}
_SHELL_FLAGS = {"-c", "/c", "-command", "--command"}
_DANGEROUS_FLAGS = {"--force", "--force-with-lease", "--hard", "--delete", "-D"}
_ROOT_TARGETS = {"/", "\\", str(Path.home()), os.environ.get("USERPROFILE", "")}


class CommandPolicy:
    """Combina allowlist declarativa com análise semântica dos argumentos."""

    def __init__(self, *, default: Decision, rules: list[dict]) -> None:
        self.default = default
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: Path) -> CommandPolicy:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(default=Decision(data.get("default", "ask")), rules=data.get("rules", []))

    def evaluate(self, argv: Sequence[str]) -> PolicyResult:
        command = tuple(str(part) for part in argv)
        if not command:
            return PolicyResult(Decision.DENY, None, "empty command")

        semantic = self._semantic_guard(command)
        if semantic is not None:
            return semantic

        matches: list[tuple[int, dict]] = []
        for rule in self.rules:
            for prefix in rule.get("prefixes", []):
                normalized = tuple(str(part) for part in prefix)
                if command[: len(normalized)] == normalized:
                    matches.append((len(normalized), rule))

        if not matches:
            return PolicyResult(self.default, None, "no allowlist rule matched")
        _, rule = max(matches, key=lambda item: item[0])
        decision = Decision(rule["decision"])
        return PolicyResult(decision, rule.get("id"), f"matched {rule.get('id')}")

    @staticmethod
    def _semantic_guard(command: tuple[str, ...]) -> PolicyResult | None:
        executable = Path(command[0]).name.lower()
        lowered = tuple(part.lower() for part in command)

        if executable in _SHELL_EXECUTORS and any(flag in lowered[1:] for flag in _SHELL_FLAGS):
            return PolicyResult(Decision.DENY, "semantic-shell", "shell interpretation is forbidden")
        if executable in {"sudo", "su"}:
            return PolicyResult(Decision.DENY, "semantic-privilege", "privilege escalation is forbidden")

        if executable == "rm":
            targets = {part for part in command[1:] if not part.startswith("-")}
            if targets & _ROOT_TARGETS or {"-rf", "-fr"} & set(command[1:]):
                return PolicyResult(Decision.DENY, "semantic-delete", "recursive deletion is forbidden")

        if executable == "git" and len(lowered) > 1:
            operation = lowered[1]
            if operation in _DESTRUCTIVE_GIT or any(flag in lowered[2:] for flag in _DANGEROUS_FLAGS):
                return PolicyResult(
                    Decision.DENY,
                    "semantic-git-destructive",
                    "destructive Git effect is forbidden",
                )
            if operation in _MUTATING_GIT:
                return PolicyResult(
                    Decision.ASK,
                    f"git-{operation}",
                    "mutating Git effect requires explicit approval",
                )

        if executable == "docker" and len(lowered) > 1:
            if lowered[1] in {"run", "exec", "push", "rm", "rmi"}:
                return PolicyResult(
                    Decision.ASK,
                    f"docker-{lowered[1]}",
                    "Docker mutation or execution requires explicit approval",
                )
            if "--privileged" in lowered or any(
                part.startswith(("--device=", "--pid=host", "--network=host")) for part in lowered
            ):
                return PolicyResult(
                    Decision.DENY,
                    "semantic-docker-escape",
                    "Docker host escape capability is forbidden",
                )

        if executable in {"terraform", "pulumi"} and len(lowered) > 1:
            if lowered[1] in {"apply", "destroy", "up"}:
                return PolicyResult(
                    Decision.COST_APPROVAL,
                    "semantic-infrastructure-cost",
                    "infrastructure mutation requires cost approval",
                )
        return None
