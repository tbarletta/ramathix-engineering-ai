from __future__ import annotations

import json
from pathlib import Path

import typer

from ..audit import AuditLog
from ..config import Settings
from ..knowledge import JsonKnowledgeStore
from ..models import ModelRouter, OllamaClient
from ..team.context import KnowledgeContextBuilder
from .workflow import AdvancedGovernanceWorkflow

governance_app = typer.Typer(help="Advanced risk and governance assessment")


@governance_app.command("assess")
def governance_assess(plan: Path) -> None:
    settings = Settings.from_env()
    payload = json.loads(plan.read_text(encoding="utf-8"))
    knowledge_repository = str(payload.get("knowledge_repository") or "")
    if not knowledge_repository:
        raise typer.BadParameter("work package is missing knowledge_repository")

    context = KnowledgeContextBuilder(
        JsonKnowledgeStore(settings.knowledge_path)
    ).build(
        knowledge_repository,
        repository_root=payload.get("knowledge_root"),
    )
    router = ModelRouter.from_yaml(settings.model_config)
    workflow = AdvancedGovernanceWorkflow(
        router=router,
        model=OllamaClient(settings.ollama_url),
        audit=AuditLog(settings.audit_path),
    )
    assessment = workflow.assess(payload, context)
    typer.echo(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))
