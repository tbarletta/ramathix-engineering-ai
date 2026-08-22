from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .knowledge import JsonKnowledgeStore, RepositoryScanner
from .knowledge.models import RepositoryInventory
from .knowledge.scanner import DEFAULT_IGNORES
from .policy import CommandPolicy

DISCOVERY_IGNORES = DEFAULT_IGNORES | {".rea"}


def discover_repository_roots(workspace: Path) -> list[Path]:
    """Return Git repositories beneath a workspace without traversing generated directories."""
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace path is not a directory: {workspace}")
    if (workspace / ".git").exists():
        return [workspace]

    repositories: list[Path] = []
    for current, directories, _ in os.walk(workspace):
        root = Path(current)
        directories[:] = [
            directory
            for directory in directories
            if directory not in DISCOVERY_IGNORES and not (root / directory).is_symlink()
        ]
        if (root / ".git").exists():
            repositories.append(root)
            directories[:] = []
    return sorted(repositories)


def map_repository(
    root: Path,
    *,
    policy: CommandPolicy,
    audit: AuditLog,
    store: JsonKnowledgeStore,
    include_git: bool = True,
) -> tuple[RepositoryInventory, Path]:
    inventory = RepositoryScanner(policy=policy, audit=audit).scan(root, include_git=include_git)
    return inventory, store.save(inventory)


def compact_knowledge(inventory: RepositoryInventory) -> dict[str, Any]:
    """Keep the conversational prompt grounded without exceeding its context budget."""
    return {
        "repository": inventory.name,
        "root": inventory.root,
        "file_count": inventory.file_count,
        "languages": inventory.languages,
        "manifests": inventory.manifests[:20],
        "facts": [
            {
                "category": fact.category,
                "name": fact.name,
                "value": fact.value,
                "confidence": fact.confidence.value,
                "evidence": fact.evidence.source,
            }
            for fact in inventory.facts[:24]
        ],
        "symbols": [
            {
                "path": symbol.path,
                "name": symbol.name,
                "kind": symbol.kind,
                "language": symbol.language,
            }
            for symbol in inventory.symbols[:24]
        ],
        "git": {
            "branch": inventory.git.branch,
            "commits_sampled": inventory.git.commits_sampled,
            "recent_commits": inventory.git.recent_commits[:5],
        },
        "warnings": inventory.warnings[:10],
    }
