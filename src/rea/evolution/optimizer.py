from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Arm:
    name: str
    trials: int = 0
    reward: float = 0.0


class ModelPromptOptimizer:
    """Deterministic UCB1 router; it changes selection, never model weights."""

    def __init__(self, path: Path, arms: list[str]) -> None:
        if not arms:
            raise ValueError("at least one candidate is required")
        self.path = path
        self.arms = self._load(arms)

    def choose(self) -> str:
        unexplored = next((item for item in self.arms.values() if item.trials == 0), None)
        if unexplored:
            return unexplored.name
        total = sum(item.trials for item in self.arms.values())
        return max(
            self.arms.values(),
            key=lambda item: item.reward / item.trials
            + math.sqrt(2 * math.log(total) / item.trials),
        ).name

    def record(self, name: str, reward: float) -> None:
        if name not in self.arms:
            raise ValueError(f"unknown candidate: {name}")
        if not -1 <= reward <= 1:
            raise ValueError("reward must be between -1 and 1")
        arm = self.arms[name]
        arm.trials += 1
        arm.reward += reward
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"arms": [asdict(item) for item in self.arms.values()]},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _load(self, candidates: list[str]) -> dict[str, Arm]:
        values = {name: Arm(name) for name in candidates}
        if self.path.exists():
            data = json.loads(self.path.read_text("utf-8"))
            for item in data.get("arms", []):
                if item["name"] in values:
                    values[item["name"]] = Arm(**item)
        return values
