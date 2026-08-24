from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from configparser import ConfigParser
from pathlib import Path
from typing import NoReturn

import typer

from .audit import AuditLog
from .config import Settings
from .conversation import ConversationAssistant, ConversationError
from .conversation_actions import (
    INTENT_CLASSIFIER_SYSTEM_PROMPT,
    INTENT_SCHEMA,
    CloneOutcome,
    ConversationActionController,
    SessionMode,
)
from .domain import Decision
from .execution import ExecutionApprovalRequired, ExecutionDenied, GovernedLocalRunner
from .github import GitHubClient, parse_github_slug
from .governance.redaction import redact_text
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
from .organization import (
    AIEngineeringManager,
    OrganizationPlanStore,
    OrganizationWorkflow,
    RfcStore,
)
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


class _WorkspaceState:
    """Mutable pointer to the directory REA is currently operating on.

    Cloning a repository mid-session moves this from an empty starting directory to the
    freshly cloned checkout, so every closure below must re-read `.root` at call time.
    """

    def __init__(self, root: Path) -> None:
        self.root = root


class _Spinner:
    """Live "REA is thinking/building/planning" status line for the conversational REPL.

    ConversationActionController and Level6Workflow report what they're about to do via a
    plain `label: str -> None` callback (`.set`); nothing else about them needs to know a
    terminal exists. Silently disabled when stderr isn't a TTY (piped output, CI logs).
    """

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, *, enabled: bool | None = None) -> None:
        self._label = "Pensando..."
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = sys.stderr.isatty() if enabled is None else enabled

    def set(self, label: str) -> None:
        with self._lock:
            self._label = label

    def _run(self) -> None:
        start = time.monotonic()
        index = 0
        while not self._stop.is_set():
            with self._lock:
                label = self._label
            elapsed = time.monotonic() - start
            frame = self._FRAMES[index % len(self._FRAMES)]
            line = f"\r{frame} REA: {label} ({elapsed:0.0f}s)"
            sys.stderr.write(line.ljust(96))
            sys.stderr.flush()
            index += 1
            self._stop.wait(0.1)
        sys.stderr.write("\r" + " " * 96 + "\r")
        sys.stderr.flush()

    def __enter__(self) -> _Spinner:
        if self._enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop and clear the spinner early — safe to call more than once (e.g. once when
        the first streamed token arrives, then again harmlessly from `__exit__`)."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1)
            self._thread = None

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _run_conversation() -> None:
    settings = Settings.from_env()
    workspace = _WorkspaceState(Path.cwd())
    knowledge = _initialize_current_repository(settings, workspace.root)
    router = ModelRouter.from_yaml(settings.model_config)
    model = OllamaClient(settings.ollama_url)
    assistant = ConversationAssistant(router=router, model=model, knowledge=knowledge)
    actions = _conversation_actions(settings, workspace, assistant, router, model)
    typer.echo("Ramathix Engineering AI — sessão conversacional local")
    typer.echo(
        "Digite /exit para encerrar. Roadmaps, Issues e execução Level 6 são governados; "
        "use /ajuda para os comandos conversacionais.\n"
    )

    try:
        while True:
            try:
                message = input(f"{_mode_prompt(actions.mode)}você> ").strip()
            except EOFError:
                typer.echo("\nSessão encerrada.")
                return

            if message.lower() in {"/exit", "/quit", "exit", "quit"}:
                typer.echo("Sessão encerrada.")
                return
            if not message:
                continue

            spinner = _Spinner()
            actions.on_status = spinner.set
            streamed = False

            def on_token(piece: str, *, _spinner: _Spinner = spinner) -> None:
                nonlocal streamed
                if not streamed:
                    _spinner.stop()
                    typer.echo("\nREA> ", nl=False)
                    streamed = True
                typer.echo(piece, nl=False)

            try:
                with spinner:
                    spinner.set("Pensando...")
                    reply = actions.handle(message)
                    if reply is None:
                        spinner.set("Conversando...")
                        reply = assistant.reply(
                            message,
                            pending_notice=_pending_notice(actions),
                            on_token=on_token,
                        )
                    else:
                        assistant.remember(message, reply)
            except ConversationError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue
            except ValueError as exc:
                typer.echo(f"REA> {exc}", err=True)
                continue

            if streamed:
                typer.echo("\n")
            else:
                typer.echo(f"\nREA> {reply}\n")
    except KeyboardInterrupt:
        typer.echo("\nSessão encerrada.")


def _mode_prompt(mode: SessionMode) -> str:
    if mode is SessionMode.DEFAULT:
        return ""
    label = "planejamento" if mode is SessionMode.PLAN else "automático"
    return f"[{label}] "


def _pending_notice(actions: ConversationActionController) -> str | None:
    if actions.pending is None:
        return None
    return (
        f"Atenção: há uma ação pendente (`{type(actions.pending).__name__}`) aguardando "
        "confirmação explícita do usuário via `/aprovar` ou `/cancelar`. Essa ação ainda NÃO "
        "foi executada. Não descreva instalações, edições de arquivo, commits ou qualquer "
        "outro passo como já concluído; se a mensagem do usuário parecer uma aprovação, "
        "instrua-o a digitar `/aprovar`."
    )


def _conversation_actions(
    settings: Settings,
    workspace: _WorkspaceState,
    assistant: ConversationAssistant,
    router: ModelRouter,
    model: OllamaClient,
) -> ConversationActionController:
    actions = ConversationActionController(
        workflow=_organization_workflow(settings, with_github=True),
        repository=_github_repository(workspace.root),
        constraints=_roadmap_constraints(assistant.knowledge),
        execute_issue=lambda number: _run_level6_issue(
            settings,
            number=number,
            repository=_github_repository(workspace.root) or "",
            workspace=workspace.root,
            base="main",
            knowledge_repo=None,
            image=None,
            approved_rules={"git-push"},
            allow_network=False,
            max_iterations=3,
            on_progress=actions.on_status,
        ),
        classify_intent=_build_intent_classifier(router, model),
    )
    actions.clone_repository = lambda slug: _clone_repository_action(
        settings, workspace, assistant, actions, slug
    )
    actions.preview_clone_target = lambda slug: str(_clone_target(workspace.root, slug))
    actions.preview_git_command = lambda argv: _preview_git_command(settings, argv)
    actions.run_git_command = lambda argv, rule_id: _run_git_command_action(
        settings, workspace, argv, rule_id
    )
    actions.read_file = lambda path: _read_file_action(workspace, path)
    actions.list_directory = lambda path: _list_directory_action(workspace, path)
    return actions


def _build_intent_classifier(
    router: ModelRouter, model: OllamaClient
) -> Callable[[str, str | None], dict[str, object]]:
    def classify(message: str, repository: str | None) -> dict[str, object]:
        target = router.resolve("utility")
        state = (
            f"Repositório local já clonado nesta sessão: `{repository}`."
            if repository
            else "Nenhum repositório clonado ainda nesta sessão."
        )
        return model.chat_json(
            model=target.model,
            system=INTENT_CLASSIFIER_SYSTEM_PROMPT,
            user=f"{state}\n\nMensagem do usuário: {message}",
            schema=INTENT_SCHEMA,
        )

    return classify


def _preview_git_command(settings: Settings, argv: list[str]) -> tuple[str, str | None]:
    policy = CommandPolicy.from_yaml(settings.command_policy)
    result = policy.evaluate(argv)
    return result.decision.value, result.rule_id


def _run_git_command_action(
    settings: Settings,
    workspace: _WorkspaceState,
    argv: list[str],
    rule_id: str | None,
) -> str:
    # Deliberately does not go through GovernedLocalRunner.run(): most git subcommands have
    # no dedicated policy rule (rule_id is None, decision falls to the "ask" default), and
    # GovernedLocalRunner can only ever be pre-approved via a named rule id. The chat layer
    # already showed this exact command and required an explicit `/aprovar` before calling
    # here, so that IS the approval — this just re-checks DENY/cost_approval defensively and
    # logs the same audit events GovernedLocalRunner would.
    policy = CommandPolicy.from_yaml(settings.command_policy)
    audit = AuditLog(settings.audit_path)
    policy_result = policy.evaluate(argv)
    audit.write(
        "command.policy_checked",
        actor="conversation_action_controller",
        data={"argv": argv, "decision": policy_result.decision, "rule": policy_result.rule_id},
    )
    if policy_result.decision is Decision.DENY:
        raise RuntimeError(policy_result.reason)
    if policy_result.decision is Decision.COST_APPROVAL:
        raise RuntimeError("esta operação exige aprovação de custo e não é suportada por aqui")

    completed = subprocess.run(
        argv,
        cwd=workspace.root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    audit.write(
        "command.executed",
        actor="conversation_action_controller",
        data={"argv": argv, "returncode": completed.returncode},
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or completed.stdout.strip() or "comando git falhou"
        )
    return completed.stdout or completed.stderr


_READ_FILE_MAX_BYTES = 200_000
_READ_FILE_MAX_CHARS = 8_000
_LIST_DIRECTORY_MAX_ENTRIES = 300


def _resolve_workspace_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise RuntimeError(f"caminho fora do repositório: {relative}") from None
    return candidate


def _read_file_action(workspace: _WorkspaceState, path: str) -> str:
    target = _resolve_workspace_path(workspace.root, path)
    if not target.is_file():
        raise RuntimeError(f"arquivo não encontrado: {path}")
    if target.stat().st_size > _READ_FILE_MAX_BYTES:
        raise RuntimeError(
            f"arquivo grande demais para exibir (limite {_READ_FILE_MAX_BYTES // 1000}KB)"
        )
    text = target.read_text(encoding="utf-8", errors="replace")
    truncated = text[:_READ_FILE_MAX_CHARS]
    if len(text) > _READ_FILE_MAX_CHARS:
        truncated += "\n... (conteúdo truncado)"
    return redact_text(truncated)


def _list_directory_action(workspace: _WorkspaceState, path: str) -> str:
    target = _resolve_workspace_path(workspace.root, path)
    if not target.is_dir():
        raise RuntimeError(f"diretório não encontrado: {path}")
    entries = sorted(
        target.iterdir(),
        key=lambda item: (item.is_file(), item.name.lower()),
    )
    lines = [f"{item.name}/" if item.is_dir() else item.name for item in entries]
    if len(lines) > _LIST_DIRECTORY_MAX_ENTRIES:
        lines = [*lines[:_LIST_DIRECTORY_MAX_ENTRIES], "... (lista truncada)"]
    return "\n".join(lines) if lines else "(vazio)"


def _clone_repository_action(
    settings: Settings,
    workspace: _WorkspaceState,
    assistant: ConversationAssistant,
    actions: ConversationActionController,
    slug: str,
) -> CloneOutcome:
    policy = CommandPolicy.from_yaml(settings.command_policy)
    audit = AuditLog(settings.audit_path)
    runner = GovernedLocalRunner(policy, audit)
    target = _clone_target(workspace.root, slug)
    if target != workspace.root and target.exists() and any(target.iterdir()):
        raise RuntimeError(f"o diretório {target} já existe e não está vazio")

    # Clone into an isolated temp dir rather than straight into `target`: `git clone`
    # refuses any non-empty destination, and REA's own `.rea/` bookkeeping folder (plus
    # whatever the governed runner's audit log recreates mid-call) already lives there.
    with tempfile.TemporaryDirectory(prefix="rea-clone-") as tmp:
        staging = Path(tmp) / "checkout"
        result = runner.run(
            ["git", "clone", f"https://github.com/{slug}.git", str(staging)],
            cwd=workspace.root,
            approved_rules={"git-clone"},
            timeout=300,
            actor="conversation_action_controller",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git clone falhou")

        target.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            destination_item = target / item.name
            if destination_item.exists():
                raise RuntimeError(
                    f"não foi possível mover `{item.name}` para `{target}`: já existe"
                )
            shutil.move(str(item), str(destination_item))

    inventory, _saved = map_repository(
        target,
        policy=policy,
        audit=audit,
        store=JsonKnowledgeStore(settings.knowledge_path),
        include_git=True,
    )
    workspace.root = target
    assistant.knowledge = compact_knowledge(inventory)
    actions.constraints = _roadmap_constraints(assistant.knowledge)
    return CloneOutcome(
        target=str(target),
        files=inventory.file_count,
        facts=len(inventory.facts),
        symbols=len(inventory.symbols),
    )


def _clone_target(root: Path, slug: str) -> Path:
    name = slug.rstrip("/").split("/")[-1]
    if _is_effectively_empty(root):
        return root
    return root / name


def _is_effectively_empty(path: Path) -> bool:
    if not path.exists():
        return True
    return not any(item.name != ".rea" for item in path.iterdir())


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


def _initialize_current_repository(settings: Settings, root: Path) -> dict | None:
    inventory, saved = map_repository(
        root,
        policy=CommandPolicy.from_yaml(settings.command_policy),
        audit=AuditLog(settings.audit_path),
        store=JsonKnowledgeStore(settings.knowledge_path),
        include_git=(root / ".git").exists(),
    )
    git_note = "" if (root / ".git").exists() else " (sem histórico Git)"
    typer.echo(
        "Mapeado "
        f"{inventory.name}: {inventory.file_count} arquivos, {len(inventory.facts)} fatos, "
        f"{len(inventory.symbols)} símbolos{git_note}.\n"
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
    on_progress: Callable[[str], None] | None = None,
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
        on_progress=on_progress,
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
        rfc_store=RfcStore(settings.home / ".rea" / "rfcs"),
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
    return parse_github_slug(url)


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
