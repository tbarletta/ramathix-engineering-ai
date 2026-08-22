from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import typer

from .audit import AuditLog
from .config import Settings
from .conversation import ConversationAssistant, ConversationError
from .execution import ExecutionApprovalRequired, ExecutionDenied
from .github import GitHubClient
from .knowledge import JsonKnowledgeStore, RepositoryScanner
from .level6 import (
    IterationLimitExceeded,
    Level6ArtifactStore,
    Level6Workflow,
    MutationBoundaryError,
    SecretDetected,
    WorkspaceBoundaryError,
)
from .models import ModelRouter, OllamaClient
from .orchestrator import TechLeadAgent
from .policy import CommandPolicy
from .sandbox import ApprovalRequired, DockerSandbox, PolicyViolation
from .team import (
    CostApprovalRequired,
    FirstTeamWorkflow,
    RequestRejected,
    WorkPackageStore,
)
from .team.context import infer_knowledge_repository

app = typer.Typer(help="Ramathix Engineering AI CLI")
models_app = typer.Typer(help="Model routing and runtime status")
policy_app = typer.Typer(help="Command governance")
issue_app = typer.Typer(help="GitHub Issue workflows")
sandbox_app = typer.Typer(help="Isolated command execution")
repo_app = typer.Typer(help="Repository knowledge and reverse engineering")
team_app = typer.Typer(help="First AI engineering team")
app.add_typer(models_app, name="models")
app.add_typer(policy_app, name="policy")
app.add_typer(issue_app, name="issue")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(repo_app, name="repo")
app.add_typer(team_app, name="team")


@app.callback(invoke_without_command=True)
def interactive_entrypoint(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _run_conversation()


@app.command("chat")
def chat() -> None:
    _run_conversation()


def _run_conversation() -> None:
    settings = Settings.from_env()
    assistant = ConversationAssistant(
        router=ModelRouter.from_yaml(settings.model_config),
        model=OllamaClient(settings.ollama_url),
    )
    typer.echo("Ramathix Engineering AI — local conversational session")
    typer.echo(
        "Type /exit to close. This chat plans and advises; governed actions require approval.\n"
    )

    try:
        while True:
            try:
                message = input("you> ").strip()
            except EOFError:
                typer.echo("\nSession closed.")
                return

            if message.lower() in {"/exit", "/quit", "exit", "quit"}:
                typer.echo("Session closed.")
                return
            if not message:
                continue

            try:
                reply = assistant.reply(message)
            except ConversationError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue
            except ValueError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue
            typer.echo(f"\nREA> {reply}\n")
    except KeyboardInterrupt:
        typer.echo("\nSession closed.")


@app.command()
def status() -> None:
    settings = Settings.from_env()
    typer.echo("Ramathix Engineering AI V0.4")
    typer.echo(f"home: {settings.home}")
    typer.echo(f"ollama: {settings.ollama_url}")
    typer.echo(f"policy: {settings.command_policy}")
    typer.echo(f"audit: {settings.audit_path}")
    typer.echo(f"knowledge: {settings.knowledge_path}")
    typer.echo(f"work: {settings.work_path}")
    typer.echo(f"worktrees: {settings.worktree_path}")


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


@issue_app.command("run")
def issue_run(
    number: int,
    repo: str = typer.Option(..., "--repo"),
    workspace: Path = typer.Option(..., "--workspace"),
    base: str = typer.Option("main", "--base"),
    knowledge_repo: str | None = typer.Option(None, "--knowledge-repo"),
    image: str | None = typer.Option(None, "--image"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
    allow_network: bool = typer.Option(False, "--allow-network/--no-network"),
    max_iterations: int = typer.Option(3, "--max-iterations", min=1, max=8),
) -> None:
    settings = Settings.from_env()
    audit = AuditLog(settings.audit_path)
    policy = CommandPolicy.from_yaml(settings.command_policy)
    github = GitHubClient()
    workspace = workspace.resolve()

    inventory = RepositoryScanner(policy=policy, audit=audit).scan(
        workspace,
        include_git=True,
    )
    JsonKnowledgeStore(settings.knowledge_path).save(inventory)
    selected_knowledge = knowledge_repo or inventory.name
    issue = github.get_issue(repo, number)
    audit.write(
        "issue.loaded",
        actor="github",
        data={"repo": repo, "issue": number, "title": issue.title},
    )
    sandbox_image = image or _infer_sandbox_image(inventory.manifests)
    worktree_root = settings.worktree_path / repo.replace("/", "__")
    workflow = _level6_workflow(settings, github)

    try:
        result = workflow.run(
            issue,
            source_workspace=workspace,
            worktree_root=worktree_root,
            knowledge_repository=selected_knowledge,
            knowledge_root=str(workspace),
            base_branch=base,
            sandbox_image=sandbox_image,
            approved_rules=set(approve_rule),
            allow_network=allow_network,
            max_iterations=max_iterations,
        )
    except CostApprovalRequired as exc:
        _print_cost_gate(exc)
    except RequestRejected as exc:
        typer.echo(
            json.dumps(
                {"status": "REJECTED", "reason": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(code=30) from exc
    except ExecutionApprovalRequired as exc:
        _print_execution_approval(
            decision=exc.decision.value,
            rule=exc.rule_id,
            command=" ".join(exc.argv),
        )
    except ApprovalRequired as exc:
        if exc.decision.value == "cost_approval":
            typer.echo(
                json.dumps(
                    {
                        "status": "COST_APPROVAL_REQUIRED",
                        "rule": exc.rule_id,
                        "command": " ".join(exc.argv),
                    },
                    indent=2,
                ),
                err=True,
            )
            raise typer.Exit(code=20) from exc
        _print_execution_approval(
            decision=exc.decision.value,
            rule=exc.rule_id,
            command=" ".join(exc.argv),
        )
    except (
        ExecutionDenied,
        PolicyViolation,
        MutationBoundaryError,
        WorkspaceBoundaryError,
        SecretDetected,
    ) as exc:
        typer.echo(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=40) from exc
    except IterationLimitExceeded as exc:
        typer.echo(
            json.dumps(
                {"status": "ITERATION_LIMIT_EXCEEDED", "reason": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=50) from exc

    typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


@repo_app.command("scan")
def repo_scan(
    path: Path,
    include_git: bool = typer.Option(True, "--git/--no-git"),
    full_json: bool = typer.Option(False, "--json"),
) -> None:
    settings = Settings.from_env()
    audit = AuditLog(settings.audit_path)
    policy = CommandPolicy.from_yaml(settings.command_policy)
    inventory = RepositoryScanner(policy=policy, audit=audit).scan(
        path, include_git=include_git
    )
    saved = JsonKnowledgeStore(settings.knowledge_path).save(inventory)
    if full_json:
        payload = inventory.to_dict()
    else:
        payload = {
            "name": inventory.name,
            "root": inventory.root,
            "files": inventory.file_count,
            "languages": inventory.languages,
            "manifests": inventory.manifests,
            "facts": len(inventory.facts),
            "dependencies": len(inventory.dependencies),
            "symbols": len(inventory.symbols),
            "git_commits": inventory.git.commits_sampled,
            "warnings": inventory.warnings,
            "saved": str(saved),
        }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@repo_app.command("list")
def repo_list() -> None:
    settings = Settings.from_env()
    repositories = JsonKnowledgeStore(settings.knowledge_path).list_repositories()
    typer.echo(json.dumps(repositories, ensure_ascii=False, indent=2))


@team_app.command("plan")
def team_plan(
    number: int,
    repo: str = typer.Option(..., "--repo"),
    knowledge_repo: str | None = typer.Option(None, "--knowledge-repo"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root"),
) -> None:
    settings = Settings.from_env()
    issue = GitHubClient().get_issue(repo, number)
    audit = AuditLog(settings.audit_path)
    audit.write(
        "issue.loaded",
        actor="github",
        data={"repo": repo, "issue": number, "title": issue.title},
    )
    workflow = _first_team_workflow(settings)
    try:
        package, saved = workflow.plan(
            issue,
            knowledge_repository=knowledge_repo or infer_knowledge_repository(repo),
            knowledge_root=knowledge_root,
        )
    except CostApprovalRequired as exc:
        _print_cost_gate(exc)
    except RequestRejected as exc:
        typer.echo(
            json.dumps({"decision": "reject", "reason": str(exc)}, ensure_ascii=False, indent=2)
        )
        raise typer.Exit(code=30) from exc

    payload = package.to_dict()
    payload["saved"] = str(saved)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@team_app.command("review")
def team_review(plan: Path) -> None:
    settings = Settings.from_env()
    workflow = _first_team_workflow(settings)
    try:
        review, saved = workflow.review(plan)
    except CostApprovalRequired as exc:
        _print_cost_gate(exc)

    payload = dict(review.raw)
    payload["saved"] = str(saved)
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if review.decision == "request_changes":
        raise typer.Exit(code=10)


@sandbox_app.command("run")
def sandbox_run(
    command: str,
    workspace: Path = typer.Option(Path("."), "--workspace"),
    image: str = typer.Option("python:3.12-slim", "--image"),
    network: bool = typer.Option(False, "--network/--no-network"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
) -> None:
    settings = Settings.from_env()
    policy = CommandPolicy.from_yaml(settings.command_policy)
    sandbox = DockerSandbox(policy, AuditLog(settings.audit_path))
    try:
        result = sandbox.run(
            image=image,
            workspace=workspace,
            argv=sandbox.parse(command),
            network=network,
            approved_rules=set(approve_rule),
        )
    except ApprovalRequired as exc:
        if exc.decision.value == "cost_approval":
            raise typer.Exit(code=20) from exc
        _print_execution_approval(
            decision=exc.decision.value,
            rule=exc.rule_id,
            command=" ".join(exc.argv),
        )
    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, err=True, nl=False)
    raise typer.Exit(code=result.returncode)


def _first_team_workflow(settings: Settings) -> FirstTeamWorkflow:
    return FirstTeamWorkflow(
        router=ModelRouter.from_yaml(settings.model_config),
        model=OllamaClient(settings.ollama_url),
        knowledge_store=JsonKnowledgeStore(settings.knowledge_path),
        work_store=WorkPackageStore(settings.work_path),
        policy=CommandPolicy.from_yaml(settings.command_policy),
        audit=AuditLog(settings.audit_path),
    )


def _level6_workflow(settings: Settings, github: GitHubClient) -> Level6Workflow:
    router = ModelRouter.from_yaml(settings.model_config)
    model = OllamaClient(settings.ollama_url)
    knowledge_store = JsonKnowledgeStore(settings.knowledge_path)
    work_store = WorkPackageStore(settings.work_path)
    policy = CommandPolicy.from_yaml(settings.command_policy)
    audit = AuditLog(settings.audit_path)
    first_team = FirstTeamWorkflow(
        router=router,
        model=model,
        knowledge_store=knowledge_store,
        work_store=work_store,
        policy=policy,
        audit=audit,
    )
    return Level6Workflow(
        first_team=first_team,
        router=router,
        model=model,
        knowledge_store=knowledge_store,
        work_store=work_store,
        policy=policy,
        audit=audit,
        github=github,
        artifact_store=Level6ArtifactStore(settings.work_path),
    )


def _infer_sandbox_image(manifests: list[str]) -> str:
    names = {Path(item).name for item in manifests}
    if "package.json" in names:
        return "node:24-bookworm-slim"
    if {"build.gradle", "build.gradle.kts"} & names:
        return "eclipse-temurin:17-jdk"
    return "python:3.12-slim"


def _print_cost_gate(exc: CostApprovalRequired) -> NoReturn:
    typer.echo(
        json.dumps(
            {
                "status": "COST_APPROVAL_REQUIRED",
                "stage": exc.stage,
                "proposal": exc.payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        err=True,
    )
    raise typer.Exit(code=20) from exc


def _print_execution_approval(
    *,
    decision: str,
    rule: str | None,
    command: str,
) -> NoReturn:
    typer.echo(
        json.dumps(
            {
                "status": "APPROVAL_REQUIRED",
                "decision": decision,
                "rule": rule,
                "command": command,
                "hint": f"rerun with --approve-rule {rule}" if rule else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        err=True,
    )
    raise typer.Exit(code=11)


if __name__ == "__main__":
    app()
