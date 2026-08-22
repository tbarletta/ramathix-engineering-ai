from pathlib import Path

from rea.audit import AuditLog
from rea.domain import Decision
from rea.initialization import (
    compact_knowledge,
    discover_repository_roots,
    map_repository,
    render_project_analysis,
)
from rea.knowledge import JsonKnowledgeStore
from rea.policy import CommandPolicy


def test_discover_repository_roots_skips_generated_directories(tmp_path: Path) -> None:
    api = tmp_path / "api"
    web = tmp_path / "web"
    ignored = tmp_path / "node_modules" / "dependency"
    for root in (api, web, ignored):
        (root / ".git").mkdir(parents=True)

    assert discover_repository_roots(tmp_path) == [api, web]


def test_map_repository_saves_compact_inventory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
    inventory, saved = map_repository(
        tmp_path,
        policy=CommandPolicy(default=Decision.ALLOW, rules=[]),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        store=JsonKnowledgeStore(tmp_path / "knowledge"),
        include_git=False,
    )

    context = compact_knowledge(inventory)

    assert saved.exists()
    assert context["repository"] == tmp_path.name
    assert context["languages"]["Python"] == 1
    assert context["symbols"][0]["name"] == "Service"
    assert "## Análise técnica" in render_project_analysis(context)
