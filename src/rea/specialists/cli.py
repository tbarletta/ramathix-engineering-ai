from __future__ import annotations

import json
from pathlib import Path

import typer

from ..config import Settings
from ..knowledge import JsonKnowledgeStore
from ..team.context import KnowledgeContextBuilder
from .router import AgentRouter, RoutingError, capabilities_payload

app = typer.Typer(help="Ramathix Engineering AI specialist routing")


@app.command("capabilities")
def show_capabilities() -> None:
    typer.echo(json.dumps(capabilities_payload(), ensure_ascii=False, indent=2))


@app.command("route-plan")
def route_plan(plan: Path) -> None:
    settings = Settings.from_env()
    package = json.loads(plan.read_text(encoding="utf-8"))
    knowledge_repository = str(
        package.get("knowledge_repository")
        or package.get("repository", "").split("/")[-1]
    )
    knowledge_root = package.get("knowledge_root")
    context = KnowledgeContextBuilder(JsonKnowledgeStore(settings.knowledge_path)).build(
        knowledge_repository,
        repository_root=str(knowledge_root) if knowledge_root else None,
    )
    tasks = list(package.get("technical_plan", {}).get("tasks", []))
    active_repository = str(package.get("repository") or context.repository_name)
    router = AgentRouter()
    try:
        assignments = router.route_plan(
            tasks,
            context=context,
            active_repository=active_repository,
        )
    except RoutingError as exc:
        typer.echo(
            json.dumps(
                {"status": "ROUTING_BLOCKED", "reason": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=60) from exc
    external = sorted(
        router.external_repositories(assignments, active_repository=active_repository)
    )
    typer.echo(
        json.dumps(
            {
                "status": "ROUTED",
                "active_repository": active_repository,
                "assignments": [item.to_dict() for item in assignments],
                "external_repositories": external,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
