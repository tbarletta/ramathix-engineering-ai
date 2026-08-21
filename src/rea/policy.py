from __future__ import annotations

from pathlib import Path
from typing import Sequence

import yaml

from .domain import Decision, PolicyResult


class CommandPolicy:
    def __init__(self, *, default: Decision, rules: list[dict]) -> None:
        self.default = default
        self.rules = rules

    @classmethod
    def from_yaml(cls, path: Path) -> "CommandPolicy":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(default=Decision(data.get("default", "ask")), rules=data.get("rules", []))

    def evaluate(self, argv: Sequence[str]) -> PolicyResult:
        command = tuple(argv)
        if not command:
            return PolicyResult(Decision.DENY, None, "empty command")

        for rule in self.rules:
            for prefix in rule.get("prefixes", []):
                prefix_tuple = tuple(str(part) for part in prefix)
                if command[: len(prefix_tuple)] == prefix_tuple:
                    decision = Decision(rule["decision"])
                    return PolicyResult(decision, rule.get("id"), f"matched {rule.get('id')}")

        return PolicyResult(self.default, None, "no allowlist rule matched")
