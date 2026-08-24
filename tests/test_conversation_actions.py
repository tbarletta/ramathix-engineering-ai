from types import SimpleNamespace

import httpx

from rea.conversation_actions import (
    CloneOutcome,
    ConversationActionController,
    is_high_risk_git_command,
)
from rea.organization import OrganizationPlan, Rfc, WorkUnit, WorkUnitState


def _plan() -> OrganizationPlan:
    first = WorkUnit(
        id="WU-001",
        project_id="PROJ-001",
        title="Medir qualidade atual",
        objective="Estabelecer uma linha de base verificável para os testes.",
        repository="tbarletta/demo",
        area="testing",
        acceptance_criteria=["A linha de base é registrada."],
        priority_rank=1,
        state=WorkUnitState.READY,
    )
    second = WorkUnit(
        id="WU-002",
        project_id="PROJ-001",
        title="Adicionar regressões",
        objective="Cobrir os fluxos críticos identificados na linha de base.",
        repository="tbarletta/demo",
        area="testing",
        dependencies=["WU-001"],
        acceptance_criteria=["Os testes de regressão passam."],
        priority_rank=2,
        state=WorkUnitState.READY,
    )
    return OrganizationPlan(
        id="org-demo",
        strategic_goal="Melhorar a qualidade",
        constraints=[],
        initiatives=[],
        projects=[],
        work_units=[first, second],
        created_at="2026-08-22T00:00:00Z",
    )


def _rfc() -> Rfc:
    return Rfc(
        id="rfc-demo",
        strategic_goal="Melhorar a qualidade",
        repository="tbarletta/demo",
        context="Contexto de teste.",
        scope_in=["Item dentro do escopo."],
        scope_out=["Item fora do escopo."],
        approach="Abordagem de teste.",
        alternatives=["Alternativa considerada."],
        risks=["Risco identificado."],
        acceptance_criteria=["Critério de aceite."],
        estimated_phases=2,
        effort_summary="Esforço baixo.",
        created_at="2026-08-22T00:00:00Z",
    )


class FakeWorkflow:
    def __init__(self) -> None:
        self.current = _plan()
        self.rfc = _rfc()
        self.rfc_calls: list[dict] = []
        self.plan_calls: list[dict] = []
        self.publish_calls: list[dict] = []

    def load(self, plan_id):
        assert plan_id == self.current.id
        return self.current

    def draft_rfc(self, strategic_goal, *, repositories, constraints):
        self.rfc_calls.append(
            {
                "goal": strategic_goal,
                "repositories": repositories,
                "constraints": constraints,
            }
        )
        return self.rfc, ".rea/rfcs/rfc-demo.json"

    def plan(self, strategic_goal, *, repositories, constraints):
        self.plan_calls.append(
            {
                "goal": strategic_goal,
                "repositories": repositories,
                "constraints": constraints,
            }
        )
        return self.current, ".rea/organization/org-demo.json"

    def publish(self, plan_id, *, approved_rules, unit_ids=None, **kwargs):
        self.publish_calls.append(
            {
                "plan_id": plan_id,
                "approved_rules": approved_rules,
                "unit_ids": unit_ids,
            }
        )
        for unit in self.current.work_units:
            if unit.id in (unit_ids or set()):
                unit.state = WorkUnitState.PUBLISHED
                unit.github_issue = {
                    "number": 101,
                    "url": "https://example.test/issues/101",
                }
        return self.current


def controller(workflow: FakeWorkflow, executions: list[int]) -> ConversationActionController:
    def execute(issue_number: int):
        executions.append(issue_number)
        return SimpleNamespace(
            status="draft_pr_created",
            branch="rea/issue-101-measure-quality",
            pull_request_url="https://example.test/pull/44",
        )

    return ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=["Use evidências confirmadas."],
        execute_issue=execute,
    )


def _propose_and_approve_roadmap(current: ConversationActionController) -> str:
    """Drive the two-step propose-a-project flow: draft an RFC, then approve it to get the
    executable roadmap — used by tests that only care about the roadmap that comes out."""
    rfc_response = current.handle("Vamos criar um roadmap de melhorias")
    assert rfc_response is not None
    assert "RFC proposta" in rfc_response

    roadmap_response = current.handle("/aprovar")
    assert roadmap_response is not None
    return roadmap_response


def test_is_high_risk_git_command_flags_known_destructive_patterns() -> None:
    assert is_high_risk_git_command(["git", "reset", "--hard", "HEAD~1"])
    assert is_high_risk_git_command(["git", "clean", "-fd"])
    assert is_high_risk_git_command(["git", "push", "--force"])
    assert is_high_risk_git_command(["git", "branch", "-D", "old-branch"])
    assert is_high_risk_git_command(["git", "rebase", "-i", "HEAD~3"])


def test_is_high_risk_git_command_does_not_flag_ordinary_operations() -> None:
    assert not is_high_risk_git_command(["git", "status"])
    assert not is_high_risk_git_command(["git", "checkout", "agent/publica-site-ramathix"])
    assert not is_high_risk_git_command(["git", "pull"])
    assert not is_high_risk_git_command(["git", "push"])


def clone_controller(
    workflow: FakeWorkflow,
    clones: list[str],
    *,
    classify_intent=None,
) -> ConversationActionController:
    def clone(slug: str) -> CloneOutcome:
        clones.append(slug)
        return CloneOutcome(target=f"/work/{slug.split('/')[-1]}", files=12, facts=3, symbols=7)

    return ConversationActionController(
        workflow=workflow,
        repository=None,
        constraints=[],
        execute_issue=lambda number: None,
        clone_repository=clone,
        preview_clone_target=lambda slug: f"/work/{slug.split('/')[-1]}",
        classify_intent=classify_intent,
    )


def test_clone_request_with_explicit_url_is_gated_behind_approval() -> None:
    workflow = FakeWorkflow()
    clones: list[str] = []
    current = clone_controller(workflow, clones)

    prepared = current.handle("Baixe localmente https://github.com/tbarletta/ramathix-web")

    assert prepared is not None
    assert "Clonagem preparada" in prepared
    assert "tbarletta/ramathix-web" in prepared
    assert clones == []

    approved = current.handle("/aprovar")

    assert approved is not None
    assert "Repositório clonado" in approved
    assert clones == ["tbarletta/ramathix-web"]
    assert current.repository == "tbarletta/ramathix-web"


def test_clone_request_remembers_a_repository_mentioned_earlier() -> None:
    workflow = FakeWorkflow()
    clones: list[str] = []
    current = clone_controller(workflow, clones)

    mention = current.handle(
        "Vamos trabalhar no projeto https://github.com/tbarletta/ramathix-web"
    )
    assert mention is None

    prepared = current.handle("Baixe localmente para trabalharmos")

    assert prepared is not None
    assert "tbarletta/ramathix-web" in prepared
    assert clones == []


def test_clone_request_without_any_repository_asks_for_one() -> None:
    workflow = FakeWorkflow()
    clones: list[str] = []
    current = clone_controller(workflow, clones)

    response = current.handle("pode clonar o repositório para mim?")

    assert response is not None
    assert "preciso da url" in response.lower()
    assert clones == []


def test_conversational_fallback_classifies_intent_the_fast_heuristics_miss() -> None:
    workflow = FakeWorkflow()
    clones: list[str] = []

    def classify(message: str, repository: str | None) -> dict:
        if "traga" in message.lower():
            return {"intent": "clone_repository", "repository": None, "phase": None}
        return {"intent": "none", "repository": None, "phase": None}

    current = clone_controller(workflow, clones, classify_intent=classify)
    current.handle("Vamos trabalhar no projeto https://github.com/tbarletta/ramathix-web")

    response = current.handle("traga esse projeto pra cá")

    assert response is not None
    assert "Clonagem preparada" in response
    assert clones == []


def git_controller(
    workflow: FakeWorkflow,
    commands: list[list[str]],
    *,
    repository: str | None,
    classify_intent,
    decision: str = "ask",
    rule_id: str | None = "git-checkout",
    output: str = "Switched to branch 'agent/publica-site-ramathix'",
) -> ConversationActionController:
    def run(argv: list[str], approved_rule_id: str | None) -> str:
        commands.append(argv)
        return output

    return ConversationActionController(
        workflow=workflow,
        repository=repository,
        constraints=[],
        execute_issue=lambda number: None,
        classify_intent=classify_intent,
        preview_git_command=lambda argv: (decision, rule_id),
        run_git_command=run,
    )


def test_ambiguous_download_wording_does_not_reclone_an_already_cloned_repository() -> None:
    """Regression test: once a repo is cloned, "baixe a branch X" must not be treated as a
    fresh `clone_repository` request just because it shares the word "baixe"."""
    workflow = FakeWorkflow()
    commands: list[list[str]] = []

    def classify(message: str, repository: str | None) -> dict:
        assert repository == "tbarletta/ramathix-web"
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["git", "checkout", "agent/publica-site-ramathix"],
        }

    current = git_controller(
        workflow,
        commands,
        repository="tbarletta/ramathix-web",
        classify_intent=classify,
    )

    prepared = current.handle("Baixe a branch agent/publica-site-ramathix")

    assert prepared is not None
    assert "Comando Git preparado" in prepared
    assert "git checkout agent/publica-site-ramathix" in prepared
    assert commands == []

    approved = current.handle("/aprovar")

    assert approved is not None
    assert "git checkout agent/publica-site-ramathix` executado" in approved
    assert "Switched to branch" in approved
    assert commands == [["git", "checkout", "agent/publica-site-ramathix"]]


def test_git_command_allowed_by_policy_runs_without_extra_approval() -> None:
    workflow = FakeWorkflow()
    commands: list[list[str]] = []

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["git", "status"],
        }

    current = git_controller(
        workflow,
        commands,
        repository="tbarletta/ramathix-web",
        classify_intent=classify,
        decision="allow",
        rule_id="git-read",
        output="nothing to commit, working tree clean",
    )

    response = current.handle("como está o status do repositório?")

    assert response is not None
    assert "working tree clean" in response
    assert commands == [["git", "status"]]
    assert current.pending is None


def test_git_command_denied_by_policy_is_not_executed() -> None:
    workflow = FakeWorkflow()
    commands: list[list[str]] = []

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["git", "pull"],
        }

    current = git_controller(
        workflow,
        commands,
        repository="tbarletta/ramathix-web",
        classify_intent=classify,
        decision="deny",
        rule_id=None,
    )

    response = current.handle("dá um pull aí")

    assert response is not None
    assert "bloqueado" in response.lower()
    assert commands == []


def test_high_risk_git_command_warns_before_asking_for_approval() -> None:
    workflow = FakeWorkflow()
    commands: list[list[str]] = []

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["git", "reset", "--hard", "HEAD~1"],
        }

    current = git_controller(
        workflow,
        commands,
        repository="tbarletta/ramathix-web",
        classify_intent=classify,
        rule_id=None,
    )

    response = current.handle("descarta o último commit local")

    assert response is not None
    assert "irreversível" in response.lower()
    assert commands == []


def test_git_command_intent_with_a_non_git_argv_is_ignored() -> None:
    workflow = FakeWorkflow()
    commands: list[list[str]] = []

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["rm", "-rf", "/"],
        }

    current = git_controller(
        workflow,
        commands,
        repository="tbarletta/ramathix-web",
        classify_intent=classify,
    )

    response = current.handle("apaga tudo")

    assert response is None
    assert commands == []


def test_natural_approval_phrasing_triggers_the_same_pending_action_as_slash_approve() -> None:
    """Regression test: "aprovado, faça" must not fall through to free chat while an action
    is pending — that's exactly what let the general model hallucinate finished work that
    was never actually run."""
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    _propose_and_approve_roadmap(current)
    prepared = current.handle("Implemente a fase 1")
    assert prepared is not None
    assert current.pending is not None

    published = current.handle("aprovado, faça")

    assert published is not None
    assert "Issues publicadas" in published
    assert workflow.publish_calls == [
        {
            "plan_id": "org-demo",
            "approved_rules": {"github-issue-create"},
            "unit_ids": {"WU-001"},
        }
    ]
    assert current.pending is None


def test_natural_cancellation_phrasing_discards_the_pending_action() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    _propose_and_approve_roadmap(current)
    current.handle("Implemente a fase 1")
    assert current.pending is not None

    cancelled = current.handle("não, cancela isso")

    assert cancelled is not None
    assert "cancelada" in cancelled.lower()
    assert current.pending is None
    assert workflow.publish_calls == []


def test_approval_phrasing_without_a_pending_action_falls_through_to_free_chat() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    response = current.handle("aprovado, muito bom o resultado")

    assert response is None


def test_conversation_action_flow_is_explicit_and_runs_level6_only_after_approval() -> None:
    workflow = FakeWorkflow()
    executions: list[int] = []
    current = controller(workflow, executions)

    roadmap = _propose_and_approve_roadmap(current)
    assert "Roadmap executável criado" in roadmap
    assert "Fase 1" in roadmap
    assert "Fase 2" in roadmap
    assert workflow.rfc_calls[0]["repositories"] == ["tbarletta/demo"]
    assert workflow.rfc_calls[0]["constraints"] == ["Use evidências confirmadas."]
    assert workflow.plan_calls[0]["repositories"] == ["tbarletta/demo"]
    assert workflow.plan_calls[0]["constraints"] == ["Use evidências confirmadas."]

    prepared = current.handle("Implemente a fase 1")
    assert prepared is not None
    assert "ainda **não** começou" in prepared
    assert workflow.publish_calls == []
    assert executions == []

    published = current.handle("/aprovar")
    assert published is not None
    assert "Issues publicadas" in published
    assert workflow.publish_calls == [
        {
            "plan_id": "org-demo",
            "approved_rules": {"github-issue-create"},
            "unit_ids": {"WU-001"},
        }
    ]
    assert executions == []

    execution = current.handle("/executar wu-001")
    assert execution is not None
    assert "Execução preparada" in execution
    assert executions == []

    completed = current.handle("/aprovar")
    assert completed is not None
    assert "Execução concluída" in completed
    assert "draft_pr_created" in completed
    assert executions == [101]


def test_phase_execution_without_a_persisted_plan_does_not_claim_progress() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    response = current.handle("Implemente a fase 1")

    assert response is not None
    assert "Não há um roadmap executável" in response
    assert workflow.publish_calls == []


def test_persisted_plan_can_be_loaded_in_a_new_conversation_session() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    response = current.handle("/usar org-demo")

    assert response is not None
    assert "Plano `org-demo` carregado" in response
    assert current.plan is workflow.current


def test_proposed_unit_is_not_published_before_governance_preflight() -> None:
    workflow = FakeWorkflow()
    workflow.current.work_units[0].state = WorkUnitState.PROPOSED
    current = controller(workflow, [])
    _propose_and_approve_roadmap(current)

    response = current.handle("Implemente a fase 1")

    assert response is not None
    assert "não passou pelo preflight" in response
    assert workflow.publish_calls == []


def test_github_publication_failure_preserves_the_pending_action() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])
    _propose_and_approve_roadmap(current)
    current.handle("Implemente a fase 1")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    workflow.publish = unavailable
    response = current.handle("/aprovar")

    assert response is not None
    assert "Não foi possível publicar as Issues no GitHub" in response
    assert current.pending is not None


def test_rfc_model_unavailability_does_not_end_the_conversation() -> None:
    workflow = FakeWorkflow()

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    workflow.draft_rfc = unavailable
    current = controller(workflow, [])

    response = current.handle("Vamos criar um roadmap de melhorias")

    assert response is not None
    assert "Ollama não está disponível" in response
    assert current.pending is None


def test_roadmap_model_unavailability_after_rfc_approval_preserves_the_pending_rfc() -> None:
    workflow = FakeWorkflow()

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    workflow.plan = unavailable
    current = controller(workflow, [])

    rfc_response = current.handle("Vamos criar um roadmap de melhorias")
    assert rfc_response is not None
    assert "RFC proposta" in rfc_response

    response = current.handle("/aprovar")

    assert response is not None
    assert "Ollama não está disponível" in response
    assert current.pending is not None


def test_rfc_content_is_shown_before_any_roadmap_is_generated() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])

    response = current.handle("Crie um roadmap para um projeto de autenticação social")

    assert response is not None
    assert "RFC proposta" in response
    assert "Contexto de teste." in response
    assert "Item dentro do escopo." in response
    assert "Item fora do escopo." in response
    assert "Alternativa considerada." in response
    assert "Risco identificado." in response
    assert "Critério de aceite." in response
    assert "2 fase(s)" in response
    assert workflow.plan_calls == []


def test_on_status_reports_before_rfc_drafting_and_roadmap_generation() -> None:
    workflow = FakeWorkflow()
    statuses: list[str] = []
    current = ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=[],
        execute_issue=lambda number: None,
        on_status=statuses.append,
    )

    current.handle("Vamos criar um roadmap de melhorias")
    current.handle("/aprovar")

    assert "Redigindo a RFC..." in statuses
    assert "Planejando o roadmap..." in statuses


def test_on_status_reports_before_cloning() -> None:
    workflow = FakeWorkflow()
    statuses: list[str] = []
    clones: list[str] = []

    def clone(slug: str) -> CloneOutcome:
        clones.append(slug)
        return CloneOutcome(target="/work/demo", files=1, facts=0, symbols=0)

    current = ConversationActionController(
        workflow=workflow,
        repository=None,
        constraints=[],
        execute_issue=lambda number: None,
        clone_repository=clone,
        on_status=statuses.append,
    )

    current.handle("Clone https://github.com/tbarletta/demo")
    current.handle("/aprovar")

    assert any("Clonando" in label for label in statuses)
    assert clones == ["tbarletta/demo"]


def test_on_status_reports_before_git_command_execution() -> None:
    workflow = FakeWorkflow()
    statuses: list[str] = []
    commands: list[list[str]] = []

    def run(argv: list[str], rule_id: str | None) -> str:
        commands.append(argv)
        return "ok"

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "git_command",
            "repository": None,
            "phase": None,
            "git_argv": ["git", "status"],
        }

    current = ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=[],
        execute_issue=lambda number: None,
        classify_intent=classify,
        preview_git_command=lambda argv: ("allow", "git-read"),
        run_git_command=run,
        on_status=statuses.append,
    )

    response = current.handle("como está o repositório?")

    assert response is not None
    assert "Entendendo o pedido..." in statuses
    assert any("git status" in label for label in statuses)
    assert commands == [["git", "status"]]


def test_read_file_intent_returns_content_without_any_approval() -> None:
    workflow = FakeWorkflow()
    reads: list[str] = []

    def read(path: str) -> str:
        reads.append(path)
        return '{"name": "demo"}'

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "read_file",
            "repository": None,
            "phase": None,
            "git_argv": None,
            "path": "package.json",
        }

    current = ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=[],
        execute_issue=lambda number: None,
        classify_intent=classify,
        read_file=read,
    )

    response = current.handle("mostra o package.json pra mim")

    assert response is not None
    assert "package.json" in response
    assert '"name": "demo"' in response
    assert reads == ["package.json"]
    assert current.pending is None


def test_list_directory_intent_returns_listing_without_any_approval() -> None:
    workflow = FakeWorkflow()
    listed: list[str] = []

    def listing(path: str) -> str:
        listed.append(path)
        return "src/\nREADME.md"

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "list_directory",
            "repository": None,
            "phase": None,
            "git_argv": None,
            "path": "src",
        }

    current = ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=[],
        execute_issue=lambda number: None,
        classify_intent=classify,
        list_directory=listing,
    )

    response = current.handle("lista os arquivos da pasta src")

    assert response is not None
    assert "src" in response
    assert "README.md" in response
    assert listed == ["src"]


def test_read_file_intent_without_a_configured_callback_says_so() -> None:
    workflow = FakeWorkflow()

    def classify(message: str, repository: str | None) -> dict:
        return {
            "intent": "read_file",
            "repository": None,
            "phase": None,
            "git_argv": None,
            "path": "README.md",
        }

    current = ConversationActionController(
        workflow=workflow,
        repository="tbarletta/demo",
        constraints=[],
        execute_issue=lambda number: None,
        classify_intent=classify,
    )

    response = current.handle("mostra o README.md")

    assert response is not None
    assert "não está disponível" in response.lower()
