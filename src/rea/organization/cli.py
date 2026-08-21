from __future__ import annotations

import json

import typer

from ..audit import AuditLog
from ..cli import app
from ..config import Settings
from ..github import GitHubClient
from ..models import ModelRouter, OllamaClient
from ..team import CostApprovalRequired
from .manager import AIEngineeringManager
from .store import OrganizationPlanStore
from .workflow import (
    GovernanceUnitApprovalRequired,
    IssuePublicationApprovalRequired,
    OrganizationBlocked,
    OrganizationWorkflow,
)


org_app = typer.Typer(help="AI Engineering Organization and portfolio management")
app.add_typer(org_app, name="org")


@org_app.command("plan")
def org_plan(
    strategic_goal: str,
    repository: list[str] = typer.Option(..., "--repo"),
    constraint: list[str] = typer.Option([], "--constraint"),
) -> None:
    workflow = _workflow(with_github=False)
    plan, saved = workflow.plan(
        strategic_goal,
        repositories=repository,
        constraints=constraint,
    )
    payload = plan.to_dict()
    payload["saved"] = saved
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@org_app.command("list")
def org_list() -> None:
    settings = Settings.from_env()
    store = OrganizationPlanStore(settings.home / ".rea" / "organization")
    typer.echo(json.dumps(store.list(), ensure_ascii=False, indent=2))


@org_app.command("show")
def org_show(plan_id: str) -> None:
    settings = Settings.from_env()
    store = OrganizationPlanStore(settings.home / ".rea" / "organization")
    typer.echo(json.dumps(store.load(plan_id).to_dict(), ensure_ascii=False, indent=2))


@org_app.command("publish")
def org_publish(
    plan_id: str,
    unit: list[str] = typer.Option([], "--unit"),
    approve_rule: list[str] = typer.Option([], "--approve-rule"),
    approve_tech_lead: list[str] = typer.Option([], "--approve-tech-lead"),
    approve_human: list[str] = typer.Option([], "--approve-human"),
    approve_cost: list[str] = typer.Option([], "--approve-cost"),
) -> None:
    workflow = _workflow(with_github=True)
    try:
        plan = workflow.publish(
            plan_id,
            approved_rules=set(approve_rule),
            unit_ids=set(unit) or None,
            tech_lead_approvals=set(approve_tech_lead),
            human_approvals=set(approve_human),
            cost_approvals=set(approve_cost),
        )
    except IssuePublicationApprovalRequired as exc:
        typer.echo(
            json.dumps(
                {
                    "status": "APPROVAL_REQUIRED",
                    "rule": "github-issue-create",
                    "reason": str(exc),
                },
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=11) from exc
    except GovernanceUnitApprovalRequired as exc:
        typer.echo(
            json.dumps(
                {
                    "status": "APPROVAL_REQUIRED",
                    "work_unit": exc.unit_id,
                    "required": exc.required,
                },
                indent=2,
            ),
            err=True,
        )
        raise typer.Exit(code=11) from exc
    except CostApprovalRequired as exc:
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
    except OrganizationBlocked as exc:
        typer.echo(
            json.dumps({"status": "BLOCKED", "reason": str(exc)}, indent=2),
            err=True,
        )
        raise typer.Exit(code=40) from exc

    typer.echo(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))


def _workflow(*, with_github: bool) -> OrganizationWorkflow:
    settings = Settings.from_env()
    router = ModelRouter.from_yaml(settings.model_config)
    model = OllamaClient(settings.ollama_url)
    return OrganizationWorkflow(
        manager=AIEngineeringManager(router, model),
        store=OrganizationPlanStore(settings.home / ".rea" / "organization"),
        audit=AuditLog(settings.audit_path),
        github=GitHubClient() if with_github else None,
    )
