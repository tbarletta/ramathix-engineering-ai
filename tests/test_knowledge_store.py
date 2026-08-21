from pathlib import Path

from rea.knowledge.models import RepositoryInventory
from rea.knowledge.store import JsonKnowledgeStore


def test_json_store_roundtrip_and_catalog(tmp_path: Path) -> None:
    store = JsonKnowledgeStore(tmp_path / "knowledge")
    inventory = RepositoryInventory(
        name="ramathix-ai-core", root="/workspace/ramathix-ai-core", file_count=42
    )
    path = store.save(inventory)
    assert path.exists()
    payload = store.load("ramathix-ai-core")
    assert payload["name"] == "ramathix-ai-core"
    assert payload["root"] == "/workspace/ramathix-ai-core"
    catalog = store.list_repositories()
    assert catalog[0]["name"] == "ramathix-ai-core"
    assert catalog[0]["file_count"] == 42


def test_catalog_keeps_multiple_repositories(tmp_path: Path) -> None:
    store = JsonKnowledgeStore(tmp_path / "knowledge")
    store.save(RepositoryInventory(name="api", root="/workspace/api"))
    store.save(RepositoryInventory(name="web", root="/workspace/web"))
    assert [item["name"] for item in store.list_repositories()] == ["api", "web"]
