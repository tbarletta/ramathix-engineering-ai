from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx

from .github import extract_github_reference
from .organization import OrganizationPlan, Rfc, WorkUnit, WorkUnitState
from .team import CostApprovalRequired


class SessionMode(StrEnum):
    """Mirrors Claude Code's own modes: PLAN never lets anything touch the repo or GitHub
    (only pure-planning artifacts — RFCs, roadmaps — are ever produced); AUTO skips the
    /aprovar prompt for local, reversible git state changes the way accepted edits are
    auto-applied, while anything external or hard to reverse (publishing Issues, Level 6
    commit/push/PR, high-risk git commands) always stays gated regardless of mode."""

    DEFAULT = "default"
    PLAN = "plan"
    AUTO = "auto"

INTENT_CLASSIFIER_SYSTEM_PROMPT = (
    "Você é o classificador de intenção do Ramathix Engineering AI (REA). Você recebe o "
    "estado atual da sessão (se já há um repositório clonado localmente) e a última mensagem "
    'do usuário; classifique a mensagem. Use "clone_repository" quando ainda NÃO há '
    "repositório local e o usuário pedir para baixar, clonar, obter, puxar, trazer, importar "
    'ou começar a trabalhar localmente em um repositório GitHub. Use "git_command" quando JÁ '
    "há um repositório local e o pedido for qualquer operação do Git sobre esse repositório: "
    "trocar de branch, ver status, histórico, diferenças, listar branches/tags/remotes, pull, "
    "fetch, merge, rebase, cherry-pick, revert, reset, stash, criar ou apagar branch/tag, "
    'commit, add, push, etc. — literalmente qualquer comando "git ...". Nesse caso preencha '
    '"git_argv" com a lista completa de argumentos do comando, sempre começando com "git" '
    '(ex.: ["git", "pull"], ["git", "checkout", "nome-da-branch"], '
    '["git", "log", "--oneline", "-10"]). Traduza o pedido do usuário para o comando Git '
    'real e completo que o realiza — não simplifique nem invente uma versão genérica. Use '
    '"create_roadmap" quando pedir um roadmap, plano de melhorias ou plano de ação '
    'executável. Use "execute_phase" quando pedir para implementar ou executar uma fase '
    'específica de um plano já existente. Use "read_file" quando o usuário pedir para ver, '
    "abrir, mostrar ou ler o conteúdo de um arquivo específico do repositório já clonado — "
    'preencha "path" com o caminho relativo exato citado (ex.: "package.json", '
    '"src/app/page.tsx"). Use "list_directory" quando pedir para listar, ver a estrutura ou '
    'os arquivos de uma pasta — preencha "path" com o caminho relativo citado, ou "." se for '
    'a raiz do repositório ou nenhuma pasta específica for citada. Use "none" para qualquer '
    'outra coisa, incluindo perguntas, dúvidas ou conversa livre. Preencha "repository" '
    'apenas se a mensagem citar um repositório de forma explícita, "phase" apenas se citar '
    'um número de fase, "git_argv" apenas para "git_command", e "path" apenas para '
    '"read_file"/"list_directory"; caso contrário deixe os campos nulos. Nunca invente um '
    "repositório, fase, branch/ref ou caminho de arquivo que o usuário não tenha citado."
)

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "clone_repository",
                "create_roadmap",
                "execute_phase",
                "git_command",
                "read_file",
                "list_directory",
                "none",
            ],
        },
        "repository": {"type": ["string", "null"]},
        "phase": {"type": ["integer", "null"]},
        "git_argv": {"type": ["array", "null"], "items": {"type": "string"}},
        "path": {"type": ["string", "null"]},
    },
    "required": ["intent", "repository", "phase", "git_argv", "path"],
}

_HIGH_RISK_GIT_MARKERS: tuple[tuple[str, ...], ...] = (
    ("reset", "--hard"),
    ("clean", "-f"),
    ("clean", "-fd"),
    ("clean", "-fdx"),
    ("clean", "-d", "-f"),
    ("push", "--force"),
    ("push", "-f"),
    ("push", "--force-with-lease"),
    ("branch", "-D"),
    ("rebase",),
    ("checkout", "--force"),
    ("checkout", "-f"),
    ("filter-branch",),
)


def is_high_risk_git_command(argv: list[str]) -> bool:
    """Best-effort heuristic to add an extra warning before a well-known destructive op.

    This never blocks anything — every git operation still runs once the user approves the
    exact command shown to them; this only makes the risk more visible beforehand.
    """
    rest = argv[1:]
    for marker in _HIGH_RISK_GIT_MARKERS:
        head, *flags = marker
        if head in rest and all(flag in rest for flag in flags):
            return True
    return False


class ConversationPlanner(Protocol):
    def load(self, plan_id: str) -> OrganizationPlan: ...

    def draft_rfc(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> tuple[Rfc, str]: ...

    def plan(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> tuple[OrganizationPlan, str]: ...

    def publish(
        self,
        plan_id: str,
        *,
        approved_rules: set[str],
        unit_ids: set[str] | None = None,
        tech_lead_approvals: set[str] | None = None,
        human_approvals: set[str] | None = None,
        cost_approvals: set[str] | None = None,
    ) -> OrganizationPlan: ...


@dataclass(frozen=True)
class PendingPublication:
    plan_id: str
    unit_ids: tuple[str, ...]


@dataclass(frozen=True)
class PendingExecution:
    unit_id: str
    issue_number: int


@dataclass(frozen=True)
class PendingClone:
    repository: str


@dataclass(frozen=True)
class PendingGitCommand:
    argv: tuple[str, ...]
    rule_id: str | None


@dataclass(frozen=True)
class PendingRfc:
    rfc_id: str
    goal: str
    repository: str


PendingAction = (
    PendingPublication | PendingExecution | PendingClone | PendingGitCommand | PendingRfc
)


@dataclass(frozen=True)
class CloneOutcome:
    target: str
    files: int
    facts: int
    symbols: int


class ConversationActionController:
    """Turns explicit chat requests into audited, approval-gated REA workflows."""

    def __init__(
        self,
        *,
        workflow: ConversationPlanner,
        repository: str | None,
        constraints: list[str],
        execute_issue: Callable[[int], Any],
        clone_repository: Callable[[str], CloneOutcome] | None = None,
        preview_clone_target: Callable[[str], str] | None = None,
        classify_intent: Callable[[str, str | None], dict[str, Any]] | None = None,
        preview_git_command: Callable[[list[str]], tuple[str, str | None]] | None = None,
        run_git_command: Callable[[list[str], str | None], str] | None = None,
        read_file: Callable[[str], str] | None = None,
        list_directory: Callable[[str], str] | None = None,
        invoke_skill: Callable[[str, dict[str, Any]], Any] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.workflow = workflow
        self.repository = repository
        self.constraints = constraints
        self.execute_issue = execute_issue
        self.clone_repository = clone_repository
        self.preview_clone_target = preview_clone_target
        self.classify_intent = classify_intent
        self.preview_git_command = preview_git_command
        self.run_git_command = run_git_command
        self.read_file = read_file
        self.list_directory = list_directory
        self.invoke_skill = invoke_skill
        self.on_status = on_status
        self.plan: OrganizationPlan | None = None
        self.pending: PendingAction | None = None
        self.mentioned_repository: str | None = repository
        self.mode: SessionMode = SessionMode.DEFAULT


    def _invoke_skill(self, message: str) -> str:
        if self.invoke_skill is None:
            return "Runtime de skills não configurado nesta sessão."
        parts = message.split(maxsplit=2)
        if len(parts) < 2:
            return "Uso: /skill ID {JSON}"
        payload: dict[str, Any] = {}
        if len(parts) == 3:
            try:
                parsed = json.loads(parts[2])
            except json.JSONDecodeError as exc:
                return f"Payload JSON inválido: {exc}"
            if not isinstance(parsed, dict):
                return "O payload da skill deve ser um objeto JSON."
            payload = parsed
        result = self.invoke_skill(parts[1], payload)
        return json.dumps(result, ensure_ascii=False, indent=2)

    def _report_status(self, label: str) -> None:
        """Report what REA is about to do, for a CLI-level progress indicator to show."""
        if self.on_status is not None:
            self.on_status(label)

    def handle(self, message: str) -> str | None:
        reference = extract_github_reference(message)
        if reference:
            self.mentioned_repository = reference
        normalized = " ".join(message.casefold().split())
        if normalized in {"/ajuda", "/help"}:
            return self._help()
        if normalized == "/status":
            return self._status()
        if (mode_request := _parse_mode_request(normalized)) is not None:
            return self._set_mode(mode_request)
        if normalized == "/cancelar":
            self.pending = None
            return "Ação pendente cancelada. Nenhuma alteração foi executada."
        if normalized == "/aprovar":
            return self._approve()
        if self.pending is not None:
            # A pending action means REA just showed the exact command/Issue/clone and asked
            # for a decision. Recognizing natural phrasing here (not just the literal
            # `/aprovar`) matters: anything that falls through to free chat while an action is
            # pending risks the general model hallucinating that the work was already done.
            if _looks_like_cancellation(normalized):
                self.pending = None
                return "Ação pendente cancelada. Nenhuma alteração foi executada."
            if _looks_like_approval(normalized):
                return self._approve()
        if message.strip().startswith("/skill "):
            return self._invoke_skill(message.strip())
        if match := re.fullmatch(r"/usar\s+([\w-]+)", normalized):
            return self._use_plan(match.group(1))
        if match := re.fullmatch(r"/executar\s+([\w-]+)", normalized):
            return self._prepare_execution(match.group(1).upper())
        if _is_clone_request(normalized) and self.repository is None:
            return self._prepare_clone(reference or self.mentioned_repository)
        if _is_roadmap_request(normalized):
            return self._propose_project(message)
        if match := re.search(r"\bfase\s*(\d+)\b", normalized):
            if _is_execution_request(normalized):
                return self._prepare_phase(int(match.group(1)))
        return self._route_conversational_intent(message)

    def _route_conversational_intent(self, message: str) -> str | None:
        """Fall back to the local model to understand phrasing the fast heuristics miss."""
        if self.classify_intent is None:
            return None
        try:
            self._report_status("Entendendo o pedido...")
            raw = self.classify_intent(message, self.repository)
        except (httpx.HTTPError, ValueError, RuntimeError, KeyError):
            return None
        intent = _validate_intent(raw)
        if intent is None:
            return None

        kind = intent.get("intent")
        if kind == "clone_repository":
            reference = intent.get("repository") or self.mentioned_repository
            return self._prepare_clone(reference)
        if kind == "create_roadmap":
            return self._propose_project(message)
        if kind == "execute_phase":
            phase = intent.get("phase")
            if not isinstance(phase, int):
                return None
            return self._prepare_phase(phase)
        if kind == "git_command":
            argv = intent.get("git_argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) for item in argv)
                or argv[0] != "git"
            ):
                return None
            return self._prepare_git_command(argv)
        if kind == "read_file":
            path = intent.get("path")
            if not isinstance(path, str) or not path.strip():
                return None
            return self._read_file(path.strip())
        if kind == "list_directory":
            path = intent.get("path")
            return self._list_directory(path.strip() if isinstance(path, str) else ".")
        return None

    def _read_file(self, path: str) -> str | None:
        if self.read_file is None:
            return "A leitura de arquivos não está disponível nesta sessão."
        try:
            self._report_status(f"Lendo `{path}`...")
            content = self.read_file(path)
        except (RuntimeError, OSError, ValueError) as exc:
            return f"Não foi possível ler `{path}`: {exc}"
        return f"## `{path}`\n\n```\n{content}\n```"

    def _list_directory(self, path: str) -> str | None:
        if self.list_directory is None:
            return "A listagem de arquivos não está disponível nesta sessão."
        try:
            self._report_status(f"Listando `{path}`...")
            listing = self.list_directory(path)
        except (RuntimeError, OSError, ValueError) as exc:
            return f"Não foi possível listar `{path}`: {exc}"
        return f"## Conteúdo de `{path}`\n\n```\n{listing}\n```"

    def _prepare_git_command(self, argv: list[str]) -> str:
        if self.run_git_command is None or self.preview_git_command is None:
            return "Operações Git diretas não estão disponíveis nesta sessão."
        command_text = " ".join(argv)
        decision, rule_id = self.preview_git_command(argv)
        if decision == "deny":
            return f"O comando `{command_text}` é bloqueado pela política de comandos do REA."
        high_risk = is_high_risk_git_command(argv)
        if decision == "allow" or (self.mode is SessionMode.AUTO and not high_risk):
            message, _ok = self._execute_git_command(argv, rule_id)
            return message

        warning = ""
        if high_risk:
            warning = (
                "\n\n⚠️ Este comando pode descartar trabalho não commitado ou reescrever "
                "histórico de forma irreversível."
            )
        self.pending = PendingGitCommand(argv=tuple(argv), rule_id=rule_id)
        return (
            f"## Comando Git preparado: `{command_text}`\n\n"
            "Esta operação altera o estado do repositório local, então pede confirmação "
            f"antes de rodar.{warning}\n\n"
            "Digite `/aprovar` para executar ou `/cancelar` para abortar."
        )

    def _execute_git_command(self, argv: list[str], rule_id: str | None) -> tuple[str, bool]:
        """Returns (message, ok) — ok=False means the caller should keep any pending action
        around so the user can retry `/aprovar` instead of losing the request on failure."""
        assert self.run_git_command is not None
        command_text = " ".join(argv)
        try:
            self._report_status(f"Executando `{command_text}`...")
            output = self.run_git_command(argv, rule_id)
        except PermissionError as exc:
            return f"Execução bloqueada por aprovação adicional: {exc}", False
        except (RuntimeError, OSError, ValueError) as exc:
            return f"Não foi possível executar `{command_text}`: {exc}", False
        return _render_git_output(command_text, output), True

    def _propose_project(self, request: str) -> str:
        """First step of proposing a project or improvement roadmap: draft an RFC and require
        approval before any roadmap, Issue or code exists — nothing is built from this call."""
        if not self.repository:
            return (
                "Não consigo propor um projeto porque o remote `origin` não aponta para um "
                "repositório GitHub reconhecido. Configure o remote e reabra o REA."
            )
        goal = _roadmap_goal(request)
        try:
            self._report_status("Redigindo a RFC...")
            rfc, saved = self.workflow.draft_rfc(
                goal,
                repositories=[self.repository],
                constraints=self.constraints,
            )
        except httpx.HTTPError:
            return (
                "O Ollama não está disponível para redigir a RFC. Inicie o serviço local e "
                "verifique `rea models status`; a sessão continua aberta."
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            return f"Não foi possível redigir a RFC: {exc}"

        self.pending = PendingRfc(rfc_id=rfc.id, goal=goal, repository=self.repository)
        return _render_rfc(rfc, saved)

    def _generate_roadmap(self, pending: PendingRfc) -> str:
        try:
            self._report_status("Planejando o roadmap...")
            plan, saved = self.workflow.plan(
                pending.goal,
                repositories=[pending.repository],
                constraints=self.constraints,
            )
        except httpx.HTTPError:
            return (
                "O Ollama não está disponível para montar o roadmap. Inicie o serviço local e "
                "verifique `rea models status`; a RFC continua aprovada e pode ser retomada "
                "com `/aprovar`."
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            return f"Não foi possível criar o roadmap executável a partir da RFC: {exc}"

        self.plan = plan
        self.pending = None
        return _render_roadmap(plan, saved)

    def _use_plan(self, plan_id: str) -> str:
        try:
            plan = self.workflow.load(plan_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Não foi possível carregar o plano `{plan_id}`: {exc}"
        repositories = {unit.repository for unit in plan.work_units}
        if self.repository and repositories != {self.repository}:
            return (
                f"O plano `{plan_id}` pertence a {sorted(repositories)}, não ao repositório "
                f"atual `{self.repository}`."
            )
        self.plan = plan
        self.pending = None
        return (
            f"Plano `{plan.id}` carregado. Diga `implemente a fase 1` ou use `/status` para "
            "consultar a sessão."
        )

    def _prepare_phase(self, phase_number: int) -> str:
        if self.plan is None:
            return (
                "Não há um roadmap executável nesta sessão. Peça para criar um roadmap primeiro; "
                "ele será decomposto em unidades de trabalho rastreáveis."
            )
        try:
            units = _phase_groups(self.plan).get(phase_number, [])
        except ValueError as exc:
            return f"O plano `{self.plan.id}` é inválido e não pode ser executado: {exc}."
        if not units:
            return f"O plano `{self.plan.id}` não possui a fase {phase_number}."

        blocked = [unit for unit in units if unit.state is WorkUnitState.BLOCKED]
        if blocked:
            return (
                "A fase não pode ser iniciada porque há unidades bloqueadas por governança: "
                + ", ".join(unit.id for unit in blocked)
                + "."
            )
        approvals = [
            unit
            for unit in units
            if unit.state
            in {WorkUnitState.GOVERNANCE_APPROVAL_REQUIRED, WorkUnitState.COST_APPROVAL_REQUIRED}
        ]
        if approvals:
            return (
                "A fase exige aprovações adicionais antes de publicar as Issues: "
                + ", ".join(f"{unit.id} ({unit.state.value})" for unit in approvals)
                + "."
            )

        not_ready = [
            unit
            for unit in units
            if unit.state not in {WorkUnitState.READY, WorkUnitState.PUBLISHED}
        ]
        if not_ready:
            return (
                "A fase ainda não passou pelo preflight de governança para: "
                + ", ".join(f"{unit.id} ({unit.state.value})" for unit in not_ready)
                + "."
            )

        unpublished = [unit for unit in units if unit.state is not WorkUnitState.PUBLISHED]
        if not unpublished:
            return self._render_published_phase(units)

        self.pending = PendingPublication(
            plan_id=self.plan.id,
            unit_ids=tuple(unit.id for unit in unpublished),
        )
        units_text = "\n".join(
            f"- `{unit.id}` — {unit.title}: {unit.objective}" for unit in unpublished
        )
        return (
            f"## Fase {phase_number} pronta para iniciar\n\n"
            "A implementação ainda **não** começou. A próxima ação criará as seguintes Issues "
            "no GitHub, uma escrita externa rastreável:\n"
            f"{units_text}\n\n"
            "Revise o escopo e digite `/aprovar` para publicar as Issues, ou `/cancelar` para "
            "abortar."
        )

    def _prepare_execution(self, unit_id: str) -> str:
        if self.plan is None:
            return "Não há plano ativo. Crie um roadmap e publique uma fase antes de executar."
        unit = next((item for item in self.plan.work_units if item.id == unit_id), None)
        if unit is None:
            return f"A unidade `{unit_id}` não pertence ao plano `{self.plan.id}`."
        if not unit.github_issue or not unit.github_issue.get("number"):
            return (
                f"A unidade `{unit.id}` ainda não possui Issue publicada. Peça para implementar "
                "a fase correspondente e aprove a publicação primeiro."
            )
        issue_number = int(unit.github_issue["number"])
        self.pending = PendingExecution(unit_id=unit.id, issue_number=issue_number)
        return (
            f"## Execução preparada: `{unit.id}`\n\n"
            f"Issue: #{issue_number} — {unit.title}\n"
            "Ao aprovar, o Level 6 criará um worktree, limitará as mudanças ao plano técnico, "
            "executará validações, fará revisão independente, criará commit, enviará a branch e "
            "abrirá um Draft PR. Produção continua bloqueada.\n\n"
            "Digite `/aprovar` para iniciar ou `/cancelar` para abortar."
        )

    def _prepare_clone(self, reference: str | None) -> str:
        if not reference:
            return (
                "Para clonar, preciso da URL ou do `owner/repositorio` do GitHub. Ex.: "
                "`https://github.com/owner/repo`."
            )
        if self.clone_repository is None:
            return "A clonagem de repositórios não está disponível nesta sessão."
        if self.mode is SessionMode.AUTO:
            message, _ok = self._execute_clone(reference)
            return message
        target_hint = ""
        if self.preview_clone_target is not None:
            target_hint = f" em `{self.preview_clone_target(reference)}`"
        self.pending = PendingClone(repository=reference)
        return (
            f"## Clonagem preparada: `{reference}`\n\n"
            f"A próxima ação executará `git clone` deste repositório{target_hint}. Nenhum "
            "arquivo foi baixado ainda.\n\n"
            "Digite `/aprovar` para clonar ou `/cancelar` para abortar."
        )

    def _execute_clone(self, reference: str) -> tuple[str, bool]:
        """Returns (message, ok) — ok=False means the caller should keep any pending action
        around so the user can retry `/aprovar` instead of losing the request on failure."""
        assert self.clone_repository is not None
        try:
            self._report_status(f"Clonando `{reference}`...")
            outcome = self.clone_repository(reference)
        except httpx.HTTPError:
            return (
                "Não foi possível clonar o repositório. Verifique a conectividade e tente "
                "novamente; nenhuma alteração local foi feita.",
                False,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return f"Não foi possível clonar o repositório: {exc}", False

        self.repository = reference
        self.mentioned_repository = reference
        return (
            f"## Repositório clonado: `{reference}`\n\n"
            f"Local: `{outcome.target}`.\n"
            f"Mapeado: {outcome.files} arquivos, {outcome.facts} fatos, "
            f"{outcome.symbols} símbolos.\n\n"
            "Já posso analisar este repositório. Peça um roadmap de melhorias quando "
            "quiser planejar o próximo passo.",
            True,
        )

    def _approve(self) -> str:
        if self.pending is None:
            return "Não há nenhuma ação pendente para aprovar."
        if isinstance(self.pending, PendingRfc):
            # Generating the roadmap is still pure planning — it only ever writes REA's own
            # .rea/organization/ bookkeeping — so it stays allowed even in Plan Mode.
            return self._generate_roadmap(self.pending)
        if self.mode is SessionMode.PLAN:
            return (
                "Modo Planejamento ativo: esta ação alteraria o repositório local ou o "
                "GitHub, então não foi executada. Digite `/modo padrao` para sair do Modo "
                "Planejamento e depois `/aprovar` de novo."
            )
        if isinstance(self.pending, PendingGitCommand):
            pending = self.pending
            message, ok = self._execute_git_command(list(pending.argv), pending.rule_id)
            if ok:
                self.pending = None
            return message
        if isinstance(self.pending, PendingClone):
            pending = self.pending
            message, ok = self._execute_clone(pending.repository)
            if ok:
                self.pending = None
            return message
        if isinstance(self.pending, PendingPublication):
            pending = self.pending
            try:
                self._report_status("Publicando Issues no GitHub...")
                self.plan = self.workflow.publish(
                    pending.plan_id,
                    approved_rules={"github-issue-create"},
                    unit_ids=set(pending.unit_ids),
                )
            except CostApprovalRequired as exc:
                return f"Ação bloqueada: aprovação de custo exigida em `{exc.stage}`."
            except httpx.HTTPError:
                return (
                    "Não foi possível publicar as Issues no GitHub. Verifique a conectividade e "
                    "o `GITHUB_TOKEN`; nenhuma execução Level 6 foi iniciada."
                )
            except (RuntimeError, ValueError) as exc:
                return f"Não foi possível publicar as Issues: {exc}"

            self.pending = None
            published = [
                unit
                for unit in self.plan.work_units
                if unit.id in pending.unit_ids and unit.github_issue
            ]
            details = "\n".join(
                _published_issue_line(unit)
                for unit in published
            )
            return (
                "## Issues publicadas\n\n"
                f"{details}\n\n"
                "Nenhum código foi alterado ainda. Para executar uma unidade, use por exemplo "
                "`/executar WU-001`; o REA exibirá um segundo gate antes de alterar código, "
                "enviar branch ou criar o Draft PR."
            )

        pending = self.pending
        try:
            self._report_status(
                "Executando Level 6 (worktree, implementação, testes, revisão, PR)..."
            )
            result = self.execute_issue(pending.issue_number)
        except CostApprovalRequired as exc:
            return f"Execução bloqueada: aprovação de custo exigida em `{exc.stage}`."
        except Exception as exc:
            return f"Execução da Issue #{pending.issue_number} bloqueada: {exc}"

        self.pending = None
        pull_request = getattr(result, "pull_request_url", None)
        branch = getattr(result, "branch", "não informada")
        status = getattr(result, "status", "concluída")
        return (
            f"## Execução concluída: `{pending.unit_id}`\n\n"
            f"- Status: `{status}`.\n"
            f"- Branch: `{branch}`.\n"
            f"- Draft PR: {pull_request or 'não criado'}.\n\n"
            "A revisão e o merge permanecem governados e exigem validação explícita."
        )

    def _render_published_phase(self, units: list[WorkUnit]) -> str:
        commands = "\n".join(f"- `/executar {unit.id}`" for unit in units)
        return (
            "As Issues desta fase já foram publicadas. Nenhum código foi alterado por essa "
            f"mensagem. Para iniciar uma unidade, escolha uma opção:\n{commands}"
        )

    def _status(self) -> str:
        plan_id = self.plan.id if self.plan else "nenhum"
        pending = type(self.pending).__name__ if self.pending else "nenhuma"
        return (
            f"Modo: `{_MODE_LABELS[self.mode]}`. Plano ativo: `{plan_id}`. "
            f"Ação pendente: `{pending}`."
        )

    def _set_mode(self, requested: str) -> str:
        if requested == "show":
            return f"Modo atual: `{_MODE_LABELS[self.mode]}`."
        if requested == "plan":
            self.mode = SessionMode.PLAN
            return (
                "## Modo Planejamento ativado\n\n"
                "REA continua analisando, redigindo RFCs e planejando roadmaps normalmente, "
                "mas `/aprovar` não executa nada que toque o repositório local ou o GitHub "
                "enquanto este modo estiver ligado. Digite `/modo padrao` para sair."
            )
        if requested == "auto":
            self.mode = SessionMode.AUTO
            return (
                "## Modo Automático ativado\n\n"
                "Operações locais e reversíveis (clonar, trocar de branch, pull, fetch, "
                "merge, stash) rodam sem pedir `/aprovar`. Continuam exigindo aprovação "
                "explícita: publicar Issues no GitHub, executar o Level 6 (commit/push/PR) e "
                "comandos Git de alto risco (reset --hard, push --force, apagar branch, "
                "rebase, clean). Digite `/modo padrao` para sair."
            )
        self.mode = SessionMode.DEFAULT
        return "## Modo padrão ativado\n\nTodas as ações voltam a exigir `/aprovar` explícito."

    @staticmethod
    def _help() -> str:
        return (
            "Comandos conversacionais:\n"
            "- peça para clonar/baixar um repositório do GitHub (cole o link) para trabalhar "
            "nele localmente;\n"
            "- com um repositório já clonado, peça qualquer operação Git sobre ele (trocar de "
            "branch, pull, fetch, merge, rebase, stash, reset, status, log, diff, criar/apagar "
            "branch ou tag, commit, push etc.) — o REA traduz para o comando `git` real;\n"
            "- peça para ver/abrir um arquivo específico, ou listar os arquivos de uma pasta — "
            "isso é só leitura e roda na hora, sem `/aprovar`;\n"
            "- peça um roadmap de melhorias ou proponha um projeto para receber uma RFC com "
            "contexto, escopo, abordagem, alternativas, riscos e estimativa — só depois de "
            "`/aprovar` a RFC o roadmap executável é gerado;\n"
            "- `implemente a fase 1` prepara as Issues da fase;\n"
            "- `/usar org-AAAA...` retoma um plano persistido;\n"
            "- `/aprovar` confirma a ação pendente;\n"
            "- `/executar WU-001` prepara a execução Level 6 de uma Issue publicada;\n"
            "- `/status` mostra o estado da sessão; `/cancelar` descarta a ação pendente;\n"
            "- `/modo planejamento` só planeja, nunca toca o repositório/GitHub; `/modo "
            "automatico` dispensa `/aprovar` para operações locais e reversíveis; `/modo "
            "padrao` volta ao comportamento normal.\n\n"
            "Frases fora desses padrões também são entendidas: o REA interpreta o pedido "
            "conversacionalmente e prepara a mesma ação governada quando reconhece a intenção. "
            "Comandos que só leem o repositório rodam na hora; qualquer comando que altere o "
            "estado do repositório (inclusive operações irreversíveis, se você pedir "
            "explicitamente) mostra o comando exato e pede `/aprovar` antes de rodar — exceto "
            "no Modo Automático, onde só as operações locais e reversíveis rodam direto."
        )


_MODE_LABELS: dict[SessionMode, str] = {
    SessionMode.DEFAULT: "padrão",
    SessionMode.PLAN: "planejamento",
    SessionMode.AUTO: "automático",
}


def _parse_mode_request(message: str) -> str | None:
    """Recognizes both the `/modo <nome>` slash form and natural phrasing, returning
    "show"/"plan"/"auto"/"default", or None if the message isn't a mode-switch request."""
    if message == "/modo":
        return "show"
    if re.fullmatch(r"/modo\s+(planejamento|plano)", message):
        return "plan"
    if re.fullmatch(r"/modo\s+(automatico|automático|auto)", message):
        return "auto"
    if re.fullmatch(r"/modo\s+(padrao|padrão|normal|default)", message):
        return "default"
    if any(
        phrase in message
        for phrase in (
            "modo planejamento",
            "modo de planejamento",
            "entra em modo planejamento",
            "ativa o modo planejamento",
            "liga o modo planejamento",
        )
    ):
        return "plan"
    if any(
        phrase in message
        for phrase in (
            "modo automatico",
            "modo automático",
            "modo auto",
            "ativa o modo automatico",
            "ativa o modo automático",
            "liga o modo automatico",
            "liga o modo automático",
        )
    ):
        return "auto"
    if any(
        phrase in message
        for phrase in (
            "modo padrao",
            "modo padrão",
            "modo normal",
            "desativa o modo",
            "sai do modo planejamento",
            "sai do modo automatico",
            "sai do modo automático",
        )
    ):
        return "default"
    return None


def _is_clone_request(message: str) -> bool:
    return any(
        word in message
        for word in (
            "clonar",
            "clone",
            "clonagem",
            "baixar",
            "baixe",
            "baixa o",
            "baixa localmente",
        )
    )


def _is_roadmap_request(message: str) -> bool:
    return "roadmap" in message and any(
        word in message for word in ("criar", "crie", "montar", "monte", "planejar", "plano")
    )


def _is_execution_request(message: str) -> bool:
    return any(word in message for word in ("implemente", "implementar", "execute", "executar"))


def _looks_like_approval(message: str) -> bool:
    return any(
        phrase in message
        for phrase in (
            "aprovado",
            "aprovada",
            "aprovo",
            "aprova",
            "tudo aprovado",
            "confirmo",
            "confirmado",
            "autorizado",
            "autorizo",
            "pode seguir",
            "pode ir",
            "pode fazer",
            "pode continuar",
            "pode prosseguir",
            "pode aprovar",
            "sim, pode",
            "sim pode",
            "ok, pode",
            "ok pode",
        )
    )


def _looks_like_cancellation(message: str) -> bool:
    return any(
        phrase in message
        for phrase in (
            "cancela",
            "cancele",
            "cancelar",
            "aborta",
            "abortar",
            "não quero",
            "nao quero",
            "não faça isso",
            "nao faca isso",
            "esquece isso",
            "deixa pra lá",
            "deixa pra la",
        )
    )


def _validate_intent(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if data.get("intent") not in {
        "clone_repository",
        "create_roadmap",
        "execute_phase",
        "git_command",
        "read_file",
        "list_directory",
        "none",
    }:
        return None
    return data


def _render_git_output(command_text: str, output: str) -> str:
    body = output.strip() or "(sem saída)"
    return f"## `{command_text}` executado\n\n```\n{body}\n```"


def _roadmap_goal(request: str) -> str:
    return (
        "Criar um roadmap de melhorias incremental e executável para o repositório atual. "
        f"Pedido do usuário: {request.strip()}"
    )


def _phase_groups(plan: OrganizationPlan) -> dict[int, list[WorkUnit]]:
    by_id = {unit.id: unit for unit in plan.work_units}
    phases: dict[str, int] = {}
    visiting: set[str] = set()

    def phase_of(unit: WorkUnit) -> int:
        if unit.id in phases:
            return phases[unit.id]
        if unit.id in visiting:
            raise ValueError(f"dependency cycle detected for {unit.id}")
        visiting.add(unit.id)
        dependencies = [by_id[item] for item in unit.dependencies if item in by_id]
        phase = 1 if not dependencies else max(phase_of(item) for item in dependencies) + 1
        visiting.remove(unit.id)
        phases[unit.id] = phase
        return phase

    grouped: dict[int, list[WorkUnit]] = {}
    for unit in sorted(plan.work_units, key=lambda item: item.priority_rank):
        grouped.setdefault(phase_of(unit), []).append(unit)
    return grouped


def _render_rfc(rfc: Rfc, saved: str) -> str:
    def _bullets(items: list[str], fallback: str) -> str:
        return "\n".join(f"- {item}" for item in items) or f"- {fallback}"

    return "\n".join(
        [
            f"## RFC proposta: `{rfc.id}`",
            "",
            f"Repositório: `{rfc.repository}`. Artefato: `{saved}`.",
            "",
            "### Contexto e objetivo",
            rfc.context,
            "",
            "### Escopo — dentro",
            _bullets(rfc.scope_in, "(não especificado)"),
            "",
            "### Escopo — fora",
            _bullets(rfc.scope_out, "(não especificado)"),
            "",
            "### Abordagem técnica",
            rfc.approach,
            "",
            "### Alternativas consideradas",
            _bullets(rfc.alternatives, "(nenhuma registrada)"),
            "",
            "### Riscos",
            _bullets(rfc.risks, "(nenhum identificado)"),
            "",
            "### Critérios de aceite",
            _bullets(rfc.acceptance_criteria, "(a definir)"),
            "",
            "### Estimativa",
            f"{rfc.effort_summary} (≈{rfc.estimated_phases} fase(s)).",
            "",
            "Nenhum roadmap, Issue, branch ou código foi criado ainda. Revise a RFC e digite "
            "`/aprovar` para gerar o roadmap executável, ou `/cancelar` para abortar.",
        ]
    )


def _render_roadmap(plan: OrganizationPlan, saved: str) -> str:
    lines = ["## Roadmap executável criado", "", f"Plano: `{plan.id}`.", f"Artefato: `{saved}`."]
    for phase, units in _phase_groups(plan).items():
        lines.extend(["", f"### Fase {phase}"])
        lines.extend(
            f"- `{unit.id}` — {unit.title} ({unit.state.value}): {unit.objective}"
            for unit in units
        )
    lines.extend(
        [
            "",
            "O roadmap foi persistido, mas nenhuma Issue, branch, código ou PR foi criado. "
            "Diga `implemente a fase 1` para preparar a primeira ação governada.",
        ]
    )
    return "\n".join(lines)


def _published_issue_line(unit: WorkUnit) -> str:
    issue = unit.github_issue or {}
    return f"- `{unit.id}` → #{issue.get('number')} ({issue.get('url', '')})"
