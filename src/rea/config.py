from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _application_config(relative_path: str) -> Path:
    """Locate bundled configuration without tying the global CLI to its working directory."""
    source_root = Path(__file__).resolve().parents[2]
    checkout_path = source_root / "config" / relative_path
    if checkout_path.is_file():
        return checkout_path
    bundled_path = Path(__file__).resolve().parent / "resources" / relative_path
    if bundled_path.is_file():
        return bundled_path
    return checkout_path


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
    def from_env(cls) -> Settings:
        home = Path(os.getenv("REA_HOME", ".")).resolve()

        def resolve_state_path(name: str, default: str) -> Path:
            raw = Path(os.getenv(name, default))
            return raw if raw.is_absolute() else home / raw

        def resolve_application_path(name: str, default: str) -> Path:
            raw = os.getenv(name)
            if raw is None:
                return _application_config(default)
            path = Path(raw)
            return path if path.is_absolute() else home / path

        return cls(
            home=home,
            ollama_url=os.getenv("REA_OLLAMA_URL", "http://localhost:11434").rstrip("/"),
            audit_path=resolve_state_path("REA_AUDIT_PATH", ".rea/audit.jsonl"),
            command_policy=resolve_application_path(
                "REA_COMMAND_POLICY", "policies/commands.yaml"
            ),
            model_config=resolve_application_path("REA_MODEL_CONFIG", "models.yaml"),
            knowledge_path=resolve_state_path("REA_KNOWLEDGE_PATH", ".rea/knowledge"),
            work_path=resolve_state_path("REA_WORK_PATH", ".rea/work"),
            worktree_path=resolve_state_path("REA_WORKTREE_PATH", ".rea/worktrees"),
        )
