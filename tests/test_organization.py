import json
from pathlib import Path

import pytest

from rea.audit import AuditLog
from rea.organization import (
    DependencyCycleError,
    GovernanceUnitApprovalRequired,
    Initiative,
    IssuePublicationApprovalRequired,
    OrganizationBlocked,
    OrganizationPlanStore,
    OrganizationWorkflow,
    PortfolioPlanner,
    Project,
    WorkUnit,
    WorkUnitState,
)
from rea.team import CostApprovalRequired


class FakeManager:
    def __init__(self, work_units: list[WorkUnit]) -> None:
        self.work_units = work_units

    def plan(self, strategic_goal, *, repositories, constraints):
        return (
            ["Prefer existing infrastructure"],
            [Initiative(id="INIT-001", title="Initiative", objective=strategic_goal)],
            [
                Project(
                    id="PROJ-001",
                    initiative_id="INIT-001",
                    title="Project",
                    objective=strategic_goal,
                    repository=repositories[0],
                )
            ],
            self.work_units,
        )


class FakeGitHub:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []

    def create_issue(self, repository: str, *, title: str, body: str):
        number = len(self.created) + 100
        self.created.append((repository, title, body))
        return {"number": number, "html_url": f"https://example.test/issues/{number}"}


def unit(
    unit_id: str,
    *,
    dependencies: list[str] | None = None,
    risk: str = "low",
    cost: bool = False,
    production_write: bool = False,
    business_value: int = 3,
) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        project_id="PROJ-001",
        title=f"Work {unit_id}",
        objective="Implement safe internal capability",
        repository="tbarletta/example",
        area="backend",
        dependencies=dependencies or [],
        acceptance_criteria=["Tests pass"],
        business_value=business_value,
        urgency=3,
        strategic_fit=4,
        effort=2,
        declared_risk=risk,
        cost_impact=cost,
        production_write=production_write,
    )


def workflow(
    tmp_path: Path,
    work_units: list[WorkUnit],
    *,
    github=None,
) -> OrganizationWorkflow:
    return OrganizationWorkflow(
        manager=FakeManager(work_units),
        store=OrganizationPlanStore(tmp_path / "org"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        github=github,
    )


def test_portfolio_prioritizes_dependencies_before_dependents() -> None:
    first = unit("WU-001", business_value=2)
    second = unit("WU-002", dependencies=["WU-001"], business_value=5)
    ordered = PortfolioPlanner().prioritize([second, first])
    assert [item.id for item in ordered] == ["WU-001", "WU-002"]
    assert ordered[0].priority_rank == 1
    assert ordered[1].priority_rank == 2


def test_portfolio_rejects_dependency_cycle() -> None:
    first = unit("WU-001", dependencies=["WU-002"])
    second = unit("WU-002", dependencies=["WU-001"])
    with pytest.raises(DependencyCycleError):
        PortfolioPlanner().prioritize([first, second])


def test_plan_blocks_declared_production_write(tmp_path: Path) -> None:
    planned, _ = workflow(
        tmp_path,
        [unit("WU-001", production_write=True)],
    ).plan(
        "Improve reliability",
        repositories=["tbarletta/example"],
        constraints=[],
    )
    assert planned.work_units[0].state is WorkUnitState.BLOCKED
    assert planned.work_units[0].governance["decision"] == "blocked"


def test_publish_requires_explicit_github_write_approval(tmp_path: Path) -> None:
    github = FakeGitHub()
    current = workflow(tmp_path, [unit("WU-001")], github=github)
    plan, _ = current.plan(
        "Improve reliability",
        repositories=["tbarletta/example"],
        constraints=[],
    )
    with pytest.raises(IssuePublicationApprovalRequired):
        current.publish(plan.id, approved_rules=set())
    assert github.created == []


def test_cost_and_high_risk_require_independent_approvals(tmp_path: Path) -> None:
    github = FakeGitHub()
    current = workflow(
        tmp_path,
        [unit("WU-001", risk="high", cost=True)],
        github=github,
    )
    plan, _ = current.plan(
        "Improve reliability",
        repositories=["tbarletta/example"],
        constraints=[],
    )

    with pytest.raises(CostApprovalRequired):
        current.publish(
            plan.id,
            approved_rules={"github-issue-create"},
        )

    with pytest.raises(GovernanceUnitApprovalRequired) as exc:
        current.publish(
            plan.id,
            approved_rules={"github-issue-create"},
            cost_approvals={"WU-001"},
        )
    assert exc.value.required == "tech-lead"

    published = current.publish(
        plan.id,
        approved_rules={"github-issue-create"},
        cost_approvals={"WU-001"},
        tech_lead_approvals={"WU-001"},
    )
    assert published.work_units[0].state is WorkUnitState.PUBLISHED
    assert len(github.created) == 1


def test_blocked_unit_cannot_be_published_with_any_approval(tmp_path: Path) -> None:
    github = FakeGitHub()
    current = workflow(
        tmp_path,
        [unit("WU-001", production_write=True)],
        github=github,
    )
    plan, _ = current.plan(
        "Improve reliability",
        repositories=["tbarletta/example"],
        constraints=[],
    )
    with pytest.raises(OrganizationBlocked):
        current.publish(
            plan.id,
            approved_rules={"github-issue-create"},
            cost_approvals={"WU-001"},
            tech_lead_approvals={"WU-001"},
            human_approvals={"WU-001"},
        )
    assert github.created == []


def test_publication_respects_dependency_order_and_persists_state(tmp_path: Path) -> None:
    github = FakeGitHub()
    current = workflow(
        tmp_path,
        [unit("WU-002", dependencies=["WU-001"]), unit("WU-001")],
        github=github,
    )
    plan, _ = current.plan(
        "Improve reliability",
        repositories=["tbarletta/example"],
        constraints=[],
    )
    published = current.publish(
        plan.id,
        approved_rules={"github-issue-create"},
    )
    assert [title for _, title, _ in github.created] == [
        "REA WU-001: Work WU-001",
        "REA WU-002: Work WU-002",
    ]
    reloaded = OrganizationPlanStore(tmp_path / "org").load(plan.id)
    assert reloaded.status == "published"
    assert all(item.state is WorkUnitState.PUBLISHED for item in reloaded.work_units)
    assert published.status == "published"


def test_strategic_secret_is_not_persisted_or_published(tmp_path: Path) -> None:
    github = FakeGitHub()
    current = workflow(tmp_path, [unit("WU-001")], github=github)
    secret = "super-sensitive-token-value-123456"
    plan, _ = current.plan(
        f"Improve reliability token={secret}",
        repositories=["tbarletta/example"],
        constraints=[f"Never expose token={secret}"],
    )

    serialized = json.dumps(plan.to_dict())
    assert secret not in serialized
    assert "REDACTED_SECRET" in serialized
    assert secret not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

    current.publish(
        plan.id,
        approved_rules={"github-issue-create"},
    )
    assert secret not in github.created[0][2]
