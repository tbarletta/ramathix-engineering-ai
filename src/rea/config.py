from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path
    ollama_url: str
    audit_path: Path
    command_policy: Path
    model_config: Path
    knowledge_path: Path
    work_path: Path
    worktree_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        home = Path(os.getenv("REA_HOME", ".")).resolve()

        def resolve_path(name: str, default: str) -> Path:
            raw = Path(os.getenv(name, default))
            return raw if raw.is_absolute() else home / raw

        return cls(
            home=home,
            ollama_url=os.getenv("REA_OLLAMA_URL", "http://localhost:11434").rstrip("/"),
            audit_path=resolve_path("REA_AUDIT_PATH", ".rea/audit.jsonl"),
            command_policy=resolve_path("REA_COMMAND_POLICY", "config/policies/commands.yaml"),
            model_config=resolve_path("REA_MODEL_CONFIG", "config/models.yaml"),
            knowledge_path=resolve_path("REA_KNOWLEDGE_PATH", ".rea/knowledge"),
            work_path=resolve_path("REA_WORK_PATH", ".rea/work"),
            worktree_path=resolve_path("REA_WORKTREE_PATH", ".rea/worktrees"),
        )
