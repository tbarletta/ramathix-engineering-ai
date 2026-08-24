from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from ..audit import AuditLog
from ..governance import GovernanceDecision, RiskEngine
from ..governance.redaction import redact_text
from ..team import CostApprovalRequired
from .contracts import OrganizationPlan, Rfc, WorkUnit, WorkUnitState
from .manager import AIEngineeringManager
from .portfolio import PortfolioPlanner
from .store import OrganizationPlanStore, RfcStore


class GitHubIssuePublisher(Protocol):
    def create_issue(
        self,
        repository: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]: ...


class IssuePublicationApprovalRequired(RuntimeError):
    pass


class GovernanceUnitApprovalRequired(RuntimeError):
    def __init__(self, unit_id: str, required: str) -> None:
        self.unit_id = unit_id
        self.required = required
        super().__init__(f"{required} approval required for {unit_id}")


class OrganizationBlocked(RuntimeError):
    pass


class OrganizationWorkflow:
    def __init__(
        self,
        *,
        manager: AIEngineeringManager,
        store: OrganizationPlanStore,
        audit: AuditLog,
        github: GitHubIssuePublisher | None = None,
        portfolio: PortfolioPlanner | None = None,
        risk_engine: RiskEngine | None = None,
        rfc_store: RfcStore | None = None,
    ) -> None:
        self.manager = manager
        self.store = store
        self.audit = audit
        self.github = github
        self.portfolio = portfolio or PortfolioPlanner()
        self.risk_engine = risk_engine or RiskEngine()
        self.rfc_store = rfc_store

    def load(self, plan_id: str) -> OrganizationPlan:
        return self.store.load(plan_id)

    def draft_rfc(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> tuple[Rfc, str]:
        if self.rfc_store is None:
            raise RuntimeError("RFC drafting is not configured")
        if not strategic_goal.strip():
            raise ValueError("strategic_goal is required")
        if len(repositories) != 1:
            raise ValueError("RFC drafting requires exactly one repository")

        safe_goal = redact_text(strategic_goal.strip())
        safe_constraints = [redact_text(item) for item in constraints]
        payload = self.manager.draft_rfc(
            safe_goal,
            repositories=repositories,
            constraints=safe_constraints,
        )
        rfc = Rfc(
            id=f"rfc-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8]}",
            strategic_goal=safe_goal,
            repository=repositories[0],
            context=str(payload["context"]),
            scope_in=[str(item) for item in payload["scope_in"]],
            scope_out=[str(item) for item in payload["scope_out"]],
            approach=str(payload["approach"]),
            alternatives=[str(item) for item in payload["alternatives"]],
            risks=[str(item) for item in payload["risks"]],
            acceptance_criteria=[str(item) for item in payload["acceptance_criteria"]],
            estimated_phases=int(payload["estimated_phases"]),
            effort_summary=str(payload["effort_summary"]),
            created_at=datetime.now(UTC).isoformat(),
        )
        path = self.rfc_store.save(rfc)
        self.audit.write(
            "organization.rfc_drafted",
            actor="engineering_manager",
            data={
                "rfc": rfc.id,
                "goal": rfc.strategic_goal,
                "repository": rfc.repository,
                "artifact": str(path),
            },
        )
        return rfc, str(path)

    def plan(
        self,
        strategic_goal: str,
        *,
        repositories: list[str],
        constraints: list[str],
    ) -> tuple[OrganizationPlan, str]:
        if not strategic_goal.strip():
            raise ValueError("strategic_goal is required")
        if not repositories:
            raise ValueError("at least one repository is required")

        safe_goal = redact_text(strategic_goal.strip())
        safe_constraints = [redact_text(item) for item in constraints]
        model_constraints, initiatives, projects, work_units = self.manager.plan(
            safe_goal,
            repositories=repositories,
            constraints=safe_constraints,
        )
        self._validate_structure(repositories, initiatives, projects, work_units)
        plan_id = f"org-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid4().hex[:8]}"
        prioritized = self.portfolio.prioritize(work_units)
        for unit in prioritized:
            self._governance_preflight(unit)
            self.audit.write(
                "organization.work_unit_prioritized",
                actor="portfolio_planner",
                data={
                    "plan": plan_id,
                    "work_unit": unit.id,
                    "priority_rank": unit.priority_rank,
                    "priority_score": unit.priority_score,
                    "state": unit.state.value,
                    "risk_level": unit.governance.get("risk_level"),
                    "decision": unit.governance.get("decision"),
                    "cost_impact": unit.cost_impact,
                },
            )

        plan = OrganizationPlan(
            id=plan_id,
            strategic_goal=safe_goal,
            constraints=list(dict.fromkeys([*safe_constraints, *model_constraints])),
            initiatives=initiatives,
            projects=projects,
            work_units=prioritized,
            created_at=datetime.now(UTC).isoformat(),
        )
        path = self.store.save(plan)
        self.audit.write(
            "organization.planned",
            actor="engineering_manager",
            data={
                "plan": plan.id,
                "goal": plan.strategic_goal,
                "initiatives": len(plan.initiatives),
                "projects": len(plan.projects),
                "work_units": len(plan.work_units),
                "artifact": str(path),
            },
        )
        return plan, str(path)

    def publish(
        self,
        plan_id: str,
        *,
        approved_rules: set[str],
        unit_ids: set[str] | None = None,
        tech_lead_approvals: set[str] | None = None,
        human_approvals: set[str] | None = None,
        cost_approvals: set[str] | None = None,
    ) -> OrganizationPlan:
        if self.github is None:
            raise RuntimeError("GitHub publisher is not configured")
        if "github-issue-create" not in approved_rules:
            self.audit.write(
                "organization.publication_blocked",
                actor="governance",
                data={"plan": plan_id, "reason": "github-issue-create approval missing"},
            )
            raise IssuePublicationApprovalRequired(
                "GitHub Issue creation requires --approve-rule github-issue-create"
            )

        plan = self.store.load(plan_id)
        selected = unit_ids or {item.id for item in plan.work_units}
        tech_lead_approvals = tech_lead_approvals or set()
        human_approvals = human_approvals or set()
        cost_approvals = cost_approvals or set()
        known = {item.id for item in plan.work_units}
        unknown = selected - known
        if unknown:
            raise ValueError(f"unknown work units: {sorted(unknown)}")

        for unit in sorted(plan.work_units, key=lambda item: item.priority_rank):
            if unit.id not in selected or unit.state is WorkUnitState.PUBLISHED:
                continue
            try:
                self._require_dependency_publication(plan, unit, selected)
                self._require_unit_approvals(
                    unit,
                    tech_lead_approvals=tech_lead_approvals,
                    human_approvals=human_approvals,
                    cost_approvals=cost_approvals,
                )
            except (
                CostApprovalRequired,
                GovernanceUnitApprovalRequired,
                OrganizationBlocked,
            ) as exc:
                self.audit.write(
                    "organization.work_unit_publication_blocked",
                    actor="governance",
                    data={
                        "plan": plan.id,
                        "work_unit": unit.id,
                        "reason": str(exc),
                    },
                )
                raise

            self.audit.write(
                "organization.work_unit_publication_gate_passed",
                actor="governance",
                data={"plan": plan.id, "work_unit": unit.id},
            )
            issue = self.github.create_issue(
                unit.repository,
                title=f"REA {unit.id}: {unit.title}",
                body=self._issue_body(plan, unit),
            )
            unit.github_issue = {
                "number": issue.get("number"),
                "url": issue.get("html_url") or issue.get("url"),
            }
            unit.state = WorkUnitState.PUBLISHED
            self.store.save(plan)
            self.audit.write(
                "organization.work_unit_published",
                actor="engineering_manager",
                data={
                    "plan": plan.id,
                    "work_unit": unit.id,
                    "repository": unit.repository,
                    "issue": unit.github_issue,
                },
            )

        if all(item.state is WorkUnitState.PUBLISHED for item in plan.work_units):
            plan.status = "published"
        elif any(item.state is WorkUnitState.PUBLISHED for item in plan.work_units):
            plan.status = "partially_published"
        self.store.save(plan)
        return plan

    def _governance_preflight(self, unit: WorkUnit) -> None:
        if unit.production_write:
            unit.governance = {
                "score": 100,
                "risk_level": "critical",
                "decision": GovernanceDecision.BLOCKED.value,
                "reasons": ["work unit declares production state change"],
            }
            unit.state = WorkUnitState.BLOCKED
            return

        assessment = self.risk_engine.assess(self._risk_payload(unit), [])
        unit.governance = assessment.to_dict()
        if assessment.decision is GovernanceDecision.BLOCKED:
            unit.state = WorkUnitState.BLOCKED
        elif assessment.decision is GovernanceDecision.COST_APPROVAL or unit.cost_impact:
            unit.state = WorkUnitState.COST_APPROVAL_REQUIRED
        elif assessment.decision in {
            GovernanceDecision.TECH_LEAD_APPROVAL,
            GovernanceDecision.HUMAN_APPROVAL,
        }:
            unit.state = WorkUnitState.GOVERNANCE_APPROVAL_REQUIRED
        else:
            unit.state = WorkUnitState.READY

    @staticmethod
    def _risk_payload(unit: WorkUnit) -> dict[str, Any]:
        return {
            "engineering_brief": {
                "objective": unit.objective,
                "risk_level": unit.declared_risk,
                "cost_impact": unit.cost_impact,
            },
            "technical_plan": {
                "architecture_summary": unit.objective,
                "risks": [],
                "cost_impact": unit.cost_impact,
                "tasks": [{"owner_role": unit.area, "description": unit.objective}],
            },
            "developer_proposal": {
                "summary": unit.objective,
                "commands": [],
                "cost_impact": unit.cost_impact,
            },
        }

    @staticmethod
    def _validate_structure(repositories, initiatives, projects, work_units) -> None:
        if not initiatives or not projects or not work_units:
            raise ValueError("organization plan must contain initiatives, projects and work units")
        initiative_ids = {item.id for item in initiatives}
        project_ids = {item.id for item in projects}
        projects_by_id = {item.id: item for item in projects}
        allowed_repositories = set(repositories)
        if len(initiative_ids) != len(initiatives):
            raise ValueError("duplicate initiative IDs")
        if len(project_ids) != len(projects):
            raise ValueError("duplicate project IDs")
        for project in projects:
            if project.initiative_id not in initiative_ids:
                raise ValueError(f"unknown initiative for project {project.id}")
            if project.repository not in allowed_repositories:
                raise ValueError(f"unapproved repository for project {project.id}")
        for unit in work_units:
            if unit.project_id not in project_ids:
                raise ValueError(f"unknown project for work unit {unit.id}")
            if unit.repository not in allowed_repositories:
                raise ValueError(f"unapproved repository for work unit {unit.id}")
            project = projects_by_id[unit.project_id]
            if unit.repository != project.repository:
                raise ValueError(
                    f"work unit {unit.id} repository differs from project {project.id}"
                )

    @staticmethod
    def _require_dependency_publication(
        plan: OrganizationPlan,
        unit: WorkUnit,
        selected: set[str],
    ) -> None:
        by_id = {item.id: item for item in plan.work_units}
        for dependency in unit.dependencies:
            dependency_unit = by_id[dependency]
            if dependency_unit.state is WorkUnitState.PUBLISHED:
                continue
            if dependency not in selected:
                raise OrganizationBlocked(
                    f"{unit.id} depends on unpublished {dependency}; include/publish it first"
                )
            raise OrganizationBlocked(
                f"{unit.id} dependency {dependency} has not been published successfully"
            )

    @staticmethod
    def _require_unit_approvals(
        unit: WorkUnit,
        *,
        tech_lead_approvals: set[str],
        human_approvals: set[str],
        cost_approvals: set[str],
    ) -> None:
        if unit.state is WorkUnitState.BLOCKED:
            raise OrganizationBlocked(f"{unit.id} is blocked by governance")

        if unit.state is WorkUnitState.COST_APPROVAL_REQUIRED:
            if unit.id not in cost_approvals:
                raise CostApprovalRequired(
                    "organization_work_unit",
                    {"work_unit": unit.id, "governance": unit.governance},
                )

        risk_level = str(unit.governance.get("risk_level", "medium"))
        if risk_level == "critical":
            if unit.id not in human_approvals:
                raise GovernanceUnitApprovalRequired(unit.id, "human")
        elif risk_level == "high":
            if unit.id not in tech_lead_approvals and unit.id not in human_approvals:
                raise GovernanceUnitApprovalRequired(unit.id, "tech-lead")

    @staticmethod
    def _issue_body(plan: OrganizationPlan, unit: WorkUnit) -> str:
        criteria = "\n".join(
            f"- {item}" for item in unit.acceptance_criteria
        ) or "- Define in refinement"
        dependencies = ", ".join(unit.dependencies) or "none"
        return (
            "## REA V1.0 Organizational Work Unit\n\n"
            f"**Organization plan:** `{plan.id}`\n"
            f"**Work unit:** `{unit.id}`\n"
            f"**Strategic goal:** {plan.strategic_goal}\n"
            f"**Area:** {unit.area}\n"
            f"**Priority:** {unit.priority_rank} (score {unit.priority_score})\n"
            f"**Dependencies:** {dependencies}\n\n"
            f"### Objective\n{unit.objective}\n\n"
            f"### Acceptance criteria\n{criteria}\n\n"
            "### Governance preflight\n"
            f"- Risk: {unit.governance.get('risk_level', 'unknown')}\n"
            f"- Decision: {unit.governance.get('decision', 'unknown')}\n"
            f"- Cost impact: {'yes' if unit.cost_impact else 'no'}\n\n"
            "Full V0.7 specialist governance is executed again immediately before Level 6."
        )
