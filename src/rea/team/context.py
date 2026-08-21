from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..knowledge.store import JsonKnowledgeStore


_CONFIDENCE_PRIORITY = {
    "confirmed": 0,
    "documented": 1,
    "inferred_high": 2,
    "inferred_medium": 3,
    "inferred_low": 4,
}


@dataclass(frozen=True)
class TeamKnowledgeContext:
    repository_name: str
    repository_root: str
    inventory: dict[str, Any]
    compact: dict[str, Any]

    def as_prompt(self) -> str:
        return json.dumps(self.compact, ensure_ascii=False, indent=2)


class KnowledgeContextBuilder:
    def __init__(self, store: JsonKnowledgeStore) -> None:
        self.store = store

    def build(
        self,
        repository_name: str,
        *,
        repository_root: str | None = None,
        max_facts: int = 80,
        max_dependencies: int = 80,
        max_symbols: int = 120,
    ) -> TeamKnowledgeContext:
        inventory = self.store.load(repository_name, root=repository_root)
        facts = sorted(
            inventory.get("facts", []),
            key=lambda item: (
                _CONFIDENCE_PRIORITY.get(str(item.get("confidence")), 99),
                str(item.get("category")),
                str(item.get("name")),
            ),
        )[:max_facts]
        dependencies = list(inventory.get("dependencies", []))[:max_dependencies]
        symbols = list(inventory.get("symbols", []))[:max_symbols]

        compact = {
            "repository": inventory.get("name"),
            "root": inventory.get("root"),
            "languages": inventory.get("languages", {}),
            "manifests": inventory.get("manifests", []),
            "facts": facts,
            "dependencies": dependencies,
            "symbols": symbols,
            "git": inventory.get("git", {}),
            "warnings": inventory.get("warnings", []),
            "knowledge_policy": {
                "priority": [
                    "human_approved_decision",
                    "approved_adr",
                    "documented_business_rule",
                    "production_code",
                    "documentation",
                    "git_history",
                    "ai_inference",
                ],
                "rule": "Never treat inferred knowledge as confirmed without human approval.",
            },
        }
        return TeamKnowledgeContext(
            repository_name=str(inventory["name"]),
            repository_root=str(inventory["root"]),
            inventory=inventory,
            compact=compact,
        )


def infer_knowledge_repository(github_repository: str) -> str:
    value = github_repository.rstrip("/")
    return value.split("/")[-1]


def load_work_package(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
