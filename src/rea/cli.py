from __future__ import annotations

import json
from configparser import ConfigParser
from pathlib import Path
from typing import NoReturn

import typer

from .audit import AuditLog
from .config import Settings
from .conversation import ConversationAssistant, ConversationError
from .conversation_actions import ConversationActionController
from .execution import ExecutionApprovalRequired, ExecutionDenied
from .github import GitHubClient
from .initialization import compact_knowledge, discover_repository_roots, map_repository
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
from .organization import AIEngineeringManager, OrganizationPlanStore, OrganizationWorkflow
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


@app.command("init")
def initialize(
    path: Path = typer.Argument(Path(".")),
    workspace: bool = typer.Option(
        False,
        "--workspace",
        help="Discover and map every Git repository below PATH.",
    ),
    include_git: bool = typer.Option(True, "--git/--no-git"),
) -> None:
    settings = Settings.from_env()
    roots = discover_repository_roots(path) if workspace else [path.resolve()]
    if not roots:
        raise typer.BadParameter("no Git repositories were found in the workspace")

    policy = CommandPolicy.from_yaml(settings.command_policy)
    audit = AuditLog(settings.audit_path)
    store = JsonKnowledgeStore(settings.knowledge_path)
    mapped = []
    for root in roots:
        inventory, saved = map_repository(
            root,
            policy=policy,
            audit=audit,
            store=store,
            include_git=include_git,
        )
        mapped.append(
            {
                "name": inventory.name,
                "root": inventory.root,
                "files": inventory.file_count,
                "languages": inventory.languages,
                "facts": len(inventory.facts),
                "dependencies": len(inventory.dependencies),
                "symbols": len(inventory.symbols),
                "saved": str(saved),
            }
        )
    typer.echo(json.dumps({"mapped": mapped}, ensure_ascii=False, indent=2))


def _run_conversation() -> None:
    settings = Settings.from_env()
    root = Path.cwd()
    knowledge = _initialize_current_repository(settings)
    assistant = ConversationAssistant(
        router=ModelRouter.from_yaml(settings.model_config),
        model=OllamaClient(settings.ollama_url),
        knowledge=knowledge,
    )
    actions = _conversation_actions(settings, root, knowledge)
    typer.echo("Ramathix Engineering AI — sessão conversacional local")
    typer.echo(
        "Digite /exit para encerrar. Roadmaps, Issues e execução Level 6 são governados; "
        "use /ajuda para os comandos conversacionais.\n"
    )

    try:
        while True:
            try:
                message = input("você> ").strip()
            except EOFError:
                typer.echo("\nSessão encerrada.")
                return

            if message.lower() in {"/exit", "/quit", "exit", "quit"}:
                typer.echo("Sessão encerrada.")
                return
            if not message:
                continue

            try:
                reply = actions.handle(message)
                if reply is None:
                    reply = assistant.reply(message)
                else:
                    assistant.remember(message, reply)
            except ConversationError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue
            except ValueError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue
            typer.echo(f"\nREA> {reply}\n")
    except KeyboardInterrupt:
        typer.echo("\nSessão encerrada.")


def _conversation_actions(
    settings: Settings,
    root: Path,
    knowledge: dict | None,
) -> ConversationActionController:
    repository = _github_repository(root)
    return ConversationActionController(
        workflow=_organization_workflow(settings, with_github=True),
        repository=repository,
        constraints=_roadmap_constraints(knowledge),
        execute_issue=lambda number: _run_level6_issue(
            settings,
            number=number,
            repository=repository or "",
            workspace=root,
            base="main",
            knowledge_repo=None,
            image=None,
            approved_rules={"git-push"},
            allow_network=False,
            max_iterations=3,
        ),
    )


def _roadmap_constraints(knowledge: dict | None) -> list[str]:
    technologies = [
        str(fact["name"])
        for fact in (knowledge or {}).get("facts", [])
        if fact.get("category") in {"framework", "infrastructure", "data", "messaging"}
    ]
    confirmed = ", ".join(technologies) or "nenhuma tecnologia adicional confirmada"
    architecture = (knowledge or {}).get("architecture") or {}
    entrypoints = ", ".join(item["name"] for item in architecture.get("entrypoints", []))
    baseline = [
        "Estado confirmado, não pendência: "
        f"{confirmed} já estão presentes no repositório e não devem ser instalados, "
        "configurados novamente ou recriados sem uma lacuna comprovada.",
        "Estado confirmado: "
        f"há {architecture.get('test_files', 0)} arquivos de teste identificados"
        + (f" e os entry points são {entrypoints}." if entrypoints else "."),
    ]
    return [
        "Use somente o repositório informado e decomponha o trabalho em unidades pequenas, "
        "testáveis e independentes quando as dependências forem atendidas.",
        f"Tecnologias confirmadas pelo inventário: {confirmed}.",
        "Não declare cobertura atual, coverage.py, pytest-cov, testes de integração ou "
        "ferramentas ausentes como existentes sem evidência. Comece por medir ou configurar "
        "o que for necessário.",
        "Não proponha custo, escrita em produção ou serviço externo sem declarar o gate "
        "correspondente.",
        "Para uma solicitação genérica de melhoria, a primeira unidade deve medir e registrar "
        "uma lacuna real; mudanças posteriores precisam depender dessa evidência.",
        *baseline,
    ]


def _initialize_current_repository(settings: Settings) -> dict | None:
    root = Path.cwd()
    if not (root / ".git").exists():
        typer.echo(
            "Nenhum repositório Git foi encontrado no diretório atual; o chat não possui "
            "inventário do repositório."
        )
        return None

    inventory, saved = map_repository(
        root,
        policy=CommandPolicy.from_yaml(settings.command_policy),
        audit=AuditLog(settings.audit_path),
        store=JsonKnowledgeStore(settings.knowledge_path),
    )
    typer.echo(
        "Mapeado "
        f"{inventory.name}: {inventory.file_count} arquivos, {len(inventory.facts)} fatos, "
        f"{len(inventory.symbols)} símbolos.\n"
        f"Conhecimento salvo em {saved}."
    )
    return compact_knowledge(inventory)


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
    try:
        result = _run_level6_issue(
            settings,
            number=number,
            repository=repo,
            workspace=workspace,
            base=base,
            knowledge_repo=knowledge_repo,
            image=image,
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


def _run_level6_issue(
    settings: Settings,
    *,
    number: int,
    repository: str,
    workspace: Path,
    base: str,
    knowledge_repo: str | None,
    image: str | None,
    approved_rules: set[str],
    allow_network: bool,
    max_iterations: int,
):
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
    issue = github.get_issue(repository, number)
    audit.write(
        "issue.loaded",
        actor="github",
        data={"repo": repository, "issue": number, "title": issue.title},
    )
    workflow = _level6_workflow(settings, github)
    return workflow.run(
        issue,
        source_workspace=workspace,
        worktree_root=settings.worktree_path / repository.replace("/", "__"),
        knowledge_repository=selected_knowledge,
        knowledge_root=str(workspace),
        base_branch=base,
        sandbox_image=image or _infer_sandbox_image(inventory.manifests),
        approved_rules=approved_rules,
        allow_network=allow_network,
        max_iterations=max_iterations,
    )


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


def _organization_workflow(
    settings: Settings,
    *,
    with_github: bool,
) -> OrganizationWorkflow:
    return OrganizationWorkflow(
        manager=AIEngineeringManager(
            ModelRouter.from_yaml(settings.model_config),
            OllamaClient(settings.ollama_url),
        ),
        store=OrganizationPlanStore(settings.home / ".rea" / "organization"),
        audit=AuditLog(settings.audit_path),
        github=GitHubClient() if with_github else None,
    )


def _github_repository(root: Path) -> str | None:
    git = root / ".git"
    config_path = git / "config"
    if git.is_file():
        marker = git.read_text(encoding="utf-8", errors="replace").strip()
        if marker.startswith("gitdir:"):
            config_path = (root / marker.removeprefix("gitdir:").strip() / "config").resolve()
    if not config_path.is_file():
        return None

    parser = ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    remote = parser['remote "origin"'] if parser.has_section('remote "origin"') else {}
    url = remote.get("url", "")
    return _github_slug(url)


def _github_slug(url: str) -> str | None:
    value = url.strip().rstrip("/")
    if "github.com:" in value:
        value = value.split("github.com:", maxsplit=1)[1]
    elif "github.com/" in value:
        value = value.split("github.com/", maxsplit=1)[1]
    else:
        return None
    slug = value.removesuffix(".git")
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return slug


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
