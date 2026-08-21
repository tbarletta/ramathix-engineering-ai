from __future__ import annotations

import json
from pathlib import Path

import typer

from .. import __version__
from ..audit import AuditLog
from ..cli import app
from ..config import Settings
from ..execution import GovernedLocalRunner
from ..governance.cli import governance_app
from ..models import ModelRouter, OllamaClient
from ..organization.cli import org_app
from ..policy import CommandPolicy
from .agent import IncidentSREAgent
from .contracts import IncidentRequest
from .policy import ProductionReadPolicy
from .providers import (
    CompositeSignalProvider,
    GitHistorySignalProvider,
    JsonFileSignalProvider,
)
from .workflow import IncidentStore, IncidentWorkflow


incident_app = typer.Typer(help="Read-only production incident analysis")
production_app = typer.Typer(help="Read-only production inspection and policy")
app.add_typer(incident_app, name="incident")
app.add_typer(production_app, name="production")
app.add_typer(governance_app, name="governance")
app.add_typer(org_app, name="org")


def _status_current() -> None:
    settings = Settings.from_env()
    typer.echo(f"Ramathix Engineering AI V{__version__}")
    typer.echo(f"home: {settings.home}")
    typer.echo(f"ollama: {settings.ollama_url}")
    typer.echo(f"policy: {settings.command_policy}")
    typer.echo(f"audit: {settings.audit_path}")
    typer.echo(f"knowledge: {settings.knowledge_path}")
    typer.echo(f"work: {settings.work_path}")
    typer.echo(f"worktrees: {settings.worktree_path}")


def _replace_status_callback() -> None:
    for command in app.registered_commands:
        callback = getattr(command, "callback", None)
        if getattr(callback, "__name__", "") == "status":
            command.callback = _status_current
            return


_replace_status_callback()


@production_app.command("policy")
def production_policy() -> None:
    typer.echo(json.dumps(ProductionReadPolicy().describe(), indent=2))


@production_app.command("inspect")
def production_inspect(
    service: str = typer.Option(..., "--service"),
    signals: list[Path] = typer.Option([], "--signals"),
    git_repo: Path | None = typer.Option(None, "--git-repo"),
) -> None:
    settings = Settings.from_env()
    workflow = _workflow(settings, signals, git_repo=git_repo)
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
    git_repo: Path | None = typer.Option(None, "--git-repo"),
) -> None:
    settings = Settings.from_env()
    workflow = _workflow(settings, signals, git_repo=git_repo)
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


def _workflow(
    settings: Settings,
    signal_paths: list[Path],
    *,
    git_repo: Path | None,
) -> IncidentWorkflow:
    if not signal_paths and git_repo is None:
        raise typer.BadParameter(
            "provide at least one --signals snapshot or a --git-repo"
        )

    policy = ProductionReadPolicy()
    audit = AuditLog(settings.audit_path)
    providers = [
        JsonFileSignalProvider(path, policy=policy)
        for path in signal_paths
    ]
    if git_repo is not None:
        command_policy = CommandPolicy.from_yaml(settings.command_policy)
        providers.append(
            GitHistorySignalProvider(
                git_repo,
                policy=policy,
                runner=GovernedLocalRunner(command_policy, audit),
            )
        )

    router = ModelRouter.from_yaml(settings.model_config)
    model = OllamaClient(settings.ollama_url)
    return IncidentWorkflow(
        provider=CompositeSignalProvider(providers),
        agent=IncidentSREAgent(router, model),
        store=IncidentStore(settings.home / ".rea" / "incidents"),
        audit=audit,
    )


if __name__ == "__main__":
    app()
