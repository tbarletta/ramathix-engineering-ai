from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .organization import OrganizationPlan, WorkUnit, WorkUnitState
from .team import CostApprovalRequired


class ConversationPlanner(Protocol):
    def load(self, plan_id: str) -> OrganizationPlan: ...

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


PendingAction = PendingPublication | PendingExecution


class ConversationActionController:
    """Turns explicit chat requests into audited, approval-gated REA workflows."""

    def __init__(
        self,
        *,
        workflow: ConversationPlanner,
        repository: str | None,
        constraints: list[str],
        execute_issue: Callable[[int], Any],
    ) -> None:
        self.workflow = workflow
        self.repository = repository
        self.constraints = constraints
        self.execute_issue = execute_issue
        self.plan: OrganizationPlan | None = None
        self.pending: PendingAction | None = None

    def handle(self, message: str) -> str | None:
        normalized = " ".join(message.casefold().split())
        if normalized in {"/ajuda", "/help"}:
            return self._help()
        if normalized == "/status":
            return self._status()
        if normalized == "/cancelar":
            self.pending = None
            return "Ação pendente cancelada. Nenhuma alteração foi executada."
        if normalized == "/aprovar":
            return self._approve()
        if match := re.fullmatch(r"/usar\s+([\w-]+)", normalized):
            return self._use_plan(match.group(1))
        if match := re.fullmatch(r"/executar\s+([\w-]+)", normalized):
            return self._prepare_execution(match.group(1).upper())
        if _is_roadmap_request(normalized):
            return self._create_roadmap(message)
        if match := re.search(r"\bfase\s*(\d+)\b", normalized):
            if _is_execution_request(normalized):
                return self._prepare_phase(int(match.group(1)))
        return None

    def _create_roadmap(self, request: str) -> str:
        if not self.repository:
            return (
                "Não consigo criar um roadmap executável porque o remote `origin` não aponta "
                "para um repositório GitHub reconhecido. Configure o remote e reabra o REA."
            )
        try:
            plan, saved = self.workflow.plan(
                _roadmap_goal(request),
                repositories=[self.repository],
                constraints=self.constraints,
            )
        except httpx.HTTPError:
            return (
                "O Ollama não está disponível para montar o roadmap. Inicie o serviço local e "
                "verifique `rea models status`; a sessão continua aberta."
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            return f"Não foi possível criar o roadmap executável: {exc}"

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

    def _approve(self) -> str:
        if self.pending is None:
            return "Não há nenhuma ação pendente para aprovar."
        if isinstance(self.pending, PendingPublication):
            pending = self.pending
            try:
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
        return f"Plano ativo: `{plan_id}`. Ação pendente: `{pending}`."

    @staticmethod
    def _help() -> str:
        return (
            "Comandos conversacionais:\n"
            "- peça um roadmap de melhorias para criar um plano executável;\n"
            "- `implemente a fase 1` prepara as Issues da fase;\n"
            "- `/usar org-AAAA...` retoma um plano persistido;\n"
            "- `/aprovar` confirma a ação pendente;\n"
            "- `/executar WU-001` prepara a execução Level 6 de uma Issue publicada;\n"
            "- `/status` mostra o estado da sessão; `/cancelar` descarta a ação pendente."
        )


def _is_roadmap_request(message: str) -> bool:
    return "roadmap" in message and any(
        word in message for word in ("criar", "crie", "montar", "monte", "planejar", "plano")
    )


def _is_execution_request(message: str) -> bool:
    return any(word in message for word in ("implemente", "implementar", "execute", "executar"))


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
