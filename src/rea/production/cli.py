from __future__ import annotations

import json
from pathlib import Path

import typer

from ..audit import AuditLog
from ..cli import app
from ..config import Settings
from ..models import ModelRouter, OllamaClient
from .agent import IncidentSREAgent
from .contracts import IncidentRequest
from .policy import ProductionReadPolicy
from .providers import CompositeSignalProvider, JsonFileSignalProvider
from .workflow import IncidentStore, IncidentWorkflow


incident_app = typer.Typer(help="Read-only production incident analysis")
production_app = typer.Typer(help="Read-only production inspection and policy")
app.add_typer(incident_app, name="incident")
app.add_typer(production_app, name="production")


@production_app.command("policy")
def production_policy() -> None:
    typer.echo(json.dumps(ProductionReadPolicy().describe(), indent=2))


@production_app.command("inspect")
def production_inspect(
    service: str = typer.Option(..., "--service"),
    signals: list[Path] = typer.Option([], "--signals"),
) -> None:
    settings = Settings.from_env()
    workflow = _workflow(settings, signals)
    request = IncidentRequest(
        incident_id="inspection",
        title=f"Production inspection: {service}",
        service=service,
        description="Read-only production signal inspection",
    )
    typer.echo(
        json.dumps(
            workflow.inspect(request),
            ensure_ascii=False,
            indent=2,
        )
    )


@incident_app.command("analyze")
def incident_analyze(
    incident_id: str,
    service: str = typer.Option(..., "--service"),
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    started_at: str | None = typer.Option(None, "--started-at"),
    signals: list[Path] = typer.Option([], "--signals"),
) -> None:
    settings = Settings.from_env()
    workflow = _workflow(settings, signals)
    request = IncidentRequest(
        incident_id=incident_id,
        title=title,
        service=service,
        description=description,
        started_at=started_at,
    )
    analysis, json_path, md_path = workflow.analyze(request)
    payload = analysis.to_dict()
    payload["json_artifact"] = str(json_path)
    payload["postmortem"] = str(md_path)
    payload["production_write_execution"] = "DISABLED"
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _workflow(settings: Settings, signal_paths: list[Path]) -> IncidentWorkflow:
    if not signal_paths:
        raise typer.BadParameter("at least one --signals JSON/JSONL snapshot is required")
    policy = ProductionReadPolicy()
    provider = CompositeSignalProvider(
        [
            JsonFileSignalProvider(path, policy=policy)
            for path in signal_paths
        ]
    )
    router = ModelRouter.from_yaml(settings.model_config)
    model = OllamaClient(settings.ollama_url)
    return IncidentWorkflow(
        provider=provider,
        agent=IncidentSREAgent(router, model),
        store=IncidentStore(settings.home / ".rea" / "incidents"),
        audit=AuditLog(settings.audit_path),
    )


if __name__ == "__main__":
    app()
