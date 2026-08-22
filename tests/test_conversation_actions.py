from types import SimpleNamespace

import httpx

from rea.conversation_actions import ConversationActionController
from rea.organization import OrganizationPlan, WorkUnit, WorkUnitState


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


class FakeWorkflow:
    def __init__(self) -> None:
        self.current = _plan()
        self.plan_calls: list[dict] = []
        self.publish_calls: list[dict] = []

    def load(self, plan_id):
        assert plan_id == self.current.id
        return self.current

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


def test_conversation_action_flow_is_explicit_and_runs_level6_only_after_approval() -> None:
    workflow = FakeWorkflow()
    executions: list[int] = []
    current = controller(workflow, executions)

    roadmap = current.handle("Vamos criar um roadmap de melhorias")
    assert roadmap is not None
    assert "Roadmap executável criado" in roadmap
    assert "Fase 1" in roadmap
    assert "Fase 2" in roadmap
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
    current.handle("Vamos criar um roadmap de melhorias")

    response = current.handle("Implemente a fase 1")

    assert response is not None
    assert "não passou pelo preflight" in response
    assert workflow.publish_calls == []


def test_github_publication_failure_preserves_the_pending_action() -> None:
    workflow = FakeWorkflow()
    current = controller(workflow, [])
    current.handle("Vamos criar um roadmap de melhorias")
    current.handle("Implemente a fase 1")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    workflow.publish = unavailable
    response = current.handle("/aprovar")

    assert response is not None
    assert "Não foi possível publicar as Issues no GitHub" in response
    assert current.pending is not None


def test_roadmap_model_unavailability_does_not_end_the_conversation() -> None:
    workflow = FakeWorkflow()

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    workflow.plan = unavailable
    current = controller(workflow, [])

    response = current.handle("Vamos criar um roadmap de melhorias")

    assert response is not None
    assert "Ollama não está disponível" in response
