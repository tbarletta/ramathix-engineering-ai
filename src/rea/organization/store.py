from __future__ import annotations

import json
from pathlib import Path

from .contracts import OrganizationPlan


class OrganizationPlanStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, plan: OrganizationPlan) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{plan.id}.json"
        path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def load(self, plan_id: str) -> OrganizationPlan:
        path = self._path(plan_id)
        return OrganizationPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[dict[str, str]]:
        if not self.root.exists():
            return []
        result: list[dict[str, str]] = []
        for path in sorted(self.root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            result.append(
                {
                    "id": str(payload.get("id", path.stem)),
                    "strategic_goal": str(payload.get("strategic_goal", "")),
                    "status": str(payload.get("status", "unknown")),
                    "path": str(path),
                }
            )
        return result

    def _path(self, plan_id: str) -> Path:
        if not plan_id or "/" in plan_id or "\\" in plan_id or ".." in plan_id:
            raise ValueError("invalid organization plan id")
        path = self.root / f"{plan_id}.json"
        if not path.is_file():
            raise FileNotFoundError(plan_id)
        return path
