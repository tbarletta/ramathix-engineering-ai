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
        "architecture": inventory.architecture,
        "git": {
            "branch": inventory.git.branch,
            "commits_sampled": inventory.git.commits_sampled,
            "recent_commits": inventory.git.recent_commits[:5],
        },
        "warnings": inventory.warnings[:10],
    }


def render_project_analysis(knowledge: dict[str, Any]) -> str:
    """Render a factual project dossier without delegating architecture inference to the LLM."""
    architecture = knowledge.get("architecture") or {}
    package = architecture.get("package") or {}
    lines = [f"## Análise técnica: {knowledge.get('repository', 'repositório')}", ""]
    lines.extend(
        [
            "### Perfil confirmado",
            f"- Pacote: `{package.get('name', knowledge.get('repository', 'não identificado'))}`.",
            f"- Arquivos inventariados: {knowledge.get('file_count', 0)}.",
        ]
    )
    if package.get("description"):
        lines.append(f"- Objetivo declarado: {package['description']}.")
    if package.get("python"):
        lines.append(f"- Runtime Python declarado: `{package['python']}`.")

    languages = knowledge.get("languages") or {}
    if languages:
        rendered_languages = ", ".join(
            f"{name} ({count})" for name, count in languages.items()
        )
        lines.append("- Linguagens: " + rendered_languages + ".")

    technologies = [
        fact
        for fact in knowledge.get("facts", [])
        if fact.get("category") in {"framework", "infrastructure", "data", "messaging"}
    ]
    if technologies:
        lines.extend(["", "### Tecnologias confirmadas"])
        lines.extend(
            f"- {fact['name']} — evidência: `{fact['evidence']}`." for fact in technologies
        )

    entrypoints = architecture.get("entrypoints") or []
    if entrypoints:
        lines.extend(["", "### Entradas operacionais"])
        lines.extend(f"- `{item['name']}` → `{item['target']}`." for item in entrypoints)

    components = architecture.get("components") or []
    if components:
        lines.extend(["", "### Componentes e estrutura"])
        lines.extend(_format_component(item) for item in components)

    external = architecture.get("external_imports") or []
    if external:
        lines.extend(["", "### Dependências importadas com maior uso"])
        lines.extend(f"- `{item['package']}` ({item['uses']} imports)." for item in external[:10])

    lines.extend(
        [
            "",
            "### Qualidade e limites",
            f"- Arquivos de teste identificados: {architecture.get('test_files', 0)}.",
            "- Relações internas de import detectadas: "
            f"{architecture.get('local_import_edges', 0)}.",
            "- Esta análise é estática e confirmada pelo inventário; comportamento em execução, "
            "cobertura e desempenho exigem testes e telemetria.",
        ]
    )
    return "\n".join(lines)


def _component_purpose(path: str) -> str:
    roles = {
        "knowledge": " — inventário e análise determinística do código",
        "governance": " — risco, políticas e gates de aprovação",
        "organization": " — planejamento de portfólio e iniciativas",
        "production": " — diagnóstico de produção somente leitura",
        "specialists": " — roteamento de especialistas de engenharia",
        "team": " — fluxo de Tech Lead, desenvolvimento e revisão",
        "level6": " — execução governada de Issue para PR",
    }
    return next((description for key, description in roles.items() if key in path), "")


def _format_component(item: dict[str, Any]) -> str:
    files = _quantity(item["source_files"], "arquivo", "arquivos")
    classes = _quantity(item["classes"], "classe", "classes")
    functions = _quantity(item["functions"], "função", "funções")
    return (
        f"- `{item['path']}`: {files} de código, {classes} e {functions}"
        f"{_component_purpose(item['path'])}."
    )


def _quantity(value: int, singular: str, plural: str) -> str:
    return f"{value} {singular if value == 1 else plural}"
