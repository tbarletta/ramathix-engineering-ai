from __future__ import annotations

import json
from pathlib import Path

import typer

from .audit import AuditLog
from .config import Settings
from .github import GitHubClient
from .models import ModelRouter, OllamaClient
from .orchestrator import TechLeadAgent
from .policy import CommandPolicy
from .sandbox import DockerSandbox

app = typer.Typer(help="Ramathix Engineering AI CLI")
models_app = typer.Typer(help="Model routing and runtime status")
policy_app = typer.Typer(help="Command governance")
issue_app = typer.Typer(help="GitHub Issue workflows")
sandbox_app = typer.Typer(help="Isolated command execution")
app.add_typer(models_app, name="models")
app.add_typer(policy_app, name="policy")
app.add_typer(issue_app, name="issue")
app.add_typer(sandbox_app, name="sandbox")


@app.command()
def status() -> None:
    settings = Settings.from_env()
    typer.echo("Ramathix Engineering AI V0.1")
    typer.echo(f"home: {settings.home}")
    typer.echo(f"ollama: {settings.ollama_url}")
    typer.echo(f"policy: {settings.command_policy}")
    typer.echo(f"audit: {settings.audit_path}")


@models_app.command("status")
def models_status() -> None:
    settings = Settings.from_env()
    router = ModelRouter.from_yaml(settings.model_config)
    client = OllamaClient(settings.ollama_url)
    installed = client.list_models()
    aliases = sorted(router.models)
    for alias in aliases:
        model = router.models[alias]["model"]
        marker = "OK" if model in installed else "MISSING"
        typer.echo(f"{alias:10} {model:24} {marker}")


@policy_app.command("check")
def policy_check(command: str) -> None:
    settings = Settings.from_env()
    policy = CommandPolicy.from_yaml(settings.command_policy)
    argv = DockerSandbox.parse(command)
    result = policy.evaluate(argv)
    typer.echo(
        json.dumps(
            {"decision": result.decision, "rule": result.rule_id, "reason": result.reason}
        )
    )


@issue_app.command("analyze")
def issue_analyze(number: int, repo: str = typer.Option(..., "--repo")) -> None:
    settings = Settings.from_env()
    audit = AuditLog(settings.audit_path)
    issue = GitHubClient().get_issue(repo, number)
    audit.write(
        "issue.loaded",
        actor="github",
        data={"repo": repo, "issue": number, "title": issue.title},
    )

    router = ModelRouter.from_yaml(settings.model_config)
    plan = TechLeadAgent(router, OllamaClient(settings.ollama_url)).analyze(issue)
    audit.write(
        "issue.analyzed",
        actor="tech_lead",
        data={"repo": repo, "issue": number, "cost_impact": plan.cost_impact},
    )
    typer.echo(json.dumps(plan.raw, ensure_ascii=False, indent=2))
    if plan.cost_impact:
        typer.echo("\nCOST_APPROVAL_REQUIRED", err=True)
        raise typer.Exit(code=20)


@sandbox_app.command("run")
def sandbox_run(
    command: str,
    workspace: Path = typer.Option(Path("."), "--workspace"),
    image: str = typer.Option("python:3.12-slim", "--image"),
    network: bool = typer.Option(False, "--network/--no-network"),
) -> None:
    settings = Settings.from_env()
    policy = CommandPolicy.from_yaml(settings.command_policy)
    sandbox = DockerSandbox(policy, AuditLog(settings.audit_path))
    result = sandbox.run(
        image=image,
        workspace=workspace,
        argv=sandbox.parse(command),
        network=network,
    )
    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, err=True, nl=False)
    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    app()
