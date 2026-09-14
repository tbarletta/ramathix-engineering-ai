from __future__ import annotations

import json
import os
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class EvolutionState:
    processed_fingerprints: list[str] = field(default_factory=list)
    active_experiment: str | None = None
    experiments_today: int = 0
    budget_date: str = ""
    gpu_minutes_today: float = 0
    consecutive_failures: int = 0
    cursor: int = 0


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> EvolutionState:
        if not self.path.exists():
            return EvolutionState()
        return EvolutionState(**json.loads(self.path.read_text("utf-8")))

    def save(self, state: EvolutionState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class RepositoryLock(AbstractContextManager):
    def __init__(self, path: Path, stale_after: int = 7200) -> None:
        self.path = path
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self) -> RepositoryLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_after:
            self.path.unlink()
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("another evolution worker owns the repository lock") from exc
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"{os.getpid()}\n")
        self.acquired = True
        return self

    def __exit__(self, *args: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
