from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

from .models import RepositoryInventory


class KnowledgeStore(Protocol):
    def save(self, inventory: RepositoryInventory) -> Path | str: ...


class JsonKnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.catalog_path = root / "catalog.json"

    def save(self, inventory: RepositoryInventory) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / self._inventory_filename(inventory.name, inventory.root)
        path.write_text(
            json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._update_catalog(inventory, path)
        return path

    def load(self, name: str, *, root: str | None = None) -> dict:
        matches = [
            item
            for item in self.list_repositories()
            if item["name"] == name and (root is None or item["root"] == root)
        ]
        if not matches:
            raise FileNotFoundError(f"repository not found in catalog: {name}")
        if len(matches) > 1:
            raise ValueError(f"multiple repositories named {name}; provide root")
        return json.loads((self.root / matches[0]["inventory"]).read_text(encoding="utf-8"))

    def list_repositories(self) -> list[dict]:
        if not self.catalog_path.exists():
            return []
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return list(payload.get("repositories", []))

    def _update_catalog(self, inventory: RepositoryInventory, path: Path) -> None:
        entries = {
            (item["name"], item["root"]): item for item in self.list_repositories()
        }
        entries[(inventory.name, inventory.root)] = {
            "name": inventory.name,
            "root": inventory.root,
            "scanned_at": inventory.scanned_at,
            "inventory": path.name,
            "file_count": inventory.file_count,
            "languages": inventory.languages,
            "facts": len(inventory.facts),
            "dependencies": len(inventory.dependencies),
            "symbols": len(inventory.symbols),
        }
        payload = {
            "repositories": sorted(
                entries.values(), key=lambda item: (item["name"], item["root"])
            )
        }
        self.catalog_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def _inventory_filename(cls, name: str, root: str) -> str:
        digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:10]
        return f"{cls._slug(name)}-{digest}.json"

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "repository"
