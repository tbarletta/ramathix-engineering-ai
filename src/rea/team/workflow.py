from __future__ import annotations

import json
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..audit import AuditLog
from ..domain import Decision, IssueWorkUnit
from ..knowledge.store import JsonKnowledgeStore
from ..policy import CommandPolicy
from .agents import (
    CodeReviewerAgent,
    CostApprovalRequired,
    EngineeringManagerAgent,
    RequestRejected,
    SeniorDeveloperAgent,
    StructuredModelClient,
    TechLeadTeamAgent,
)
from .context import KnowledgeContextBuilder
from .contracts import CodeReview, WorkPackage


class WorkPackageStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_plan(self, package: WorkPackage) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"issue-{package.issue_number}-plan.json"
        path.write_text(
            json.dumps(package.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def save_review(self, issue_number: int, review: CodeReview) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"issue-{issue_number}-review.json"
        path.write_text(
            json.dumps(asdict(review), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


class FirstTeamWorkflow:
    def __init__(
        self,
        *,
        router,
        model: StructuredModelClient,
        knowledge_store: JsonKnowledgeStore,
        work_store: WorkPackageStore,
        policy: CommandPolicy,
        audit: AuditLog,
    ) -> None:
        self.knowledge = KnowledgeContextBuilder(knowledge_store)
        self.work_store = work_store
        self.policy = policy
        self.audit = audit
        self.engineering_manager = EngineeringManagerAgent(router, model)
        self.tech_lead = TechLeadTeamAgent(router, model)
        self.senior_developer = SeniorDeveloperAgent(router, model)
        self.code_reviewer = CodeReviewerAgent(router, model)

    def plan(
        self,
        issue: IssueWorkUnit,
        *,
        knowledge_repository: str,
        knowledge_root: str | None = None,
    ) -> tuple[WorkPackage, Path]:
        context = self.knowledge.build(
            knowledge_repository,
            repository_root=knowledge_root,
        )
        self.audit.write(
            "team.context.loaded",
            actor="knowledge_engine",
            data={
                "issue": issue.number,
                "repository": context.repository_name,
                "root": context.repository_root,
            },
        )

        brief = self.engineering_manager.create_brief(issue, context)
        self._audit_stage("engineering_manager", issue.number, brief.raw)
        self._require_no_cost("engineering_manager", brief.cost_impact, brief.raw)

        plan = self.tech_lead.plan(issue, brief, context)
        self._audit_stage("tech_lead", issue.number, plan.raw)
        self._require_no_cost("tech_lead", plan.cost_impact, plan.raw)
        if plan.decision != "approve":
            self.audit.write(
                "team.blocked",
                actor="tech_lead",
                data={"issue": issue.number, "reason": plan.rationale},
            )
            raise RequestRejected(plan.rationale)

        proposal = self.senior_developer.propose(issue, brief, plan, context)
        self._audit_stage("senior_developer", issue.number, proposal.raw)
        self._require_no_cost("senior_developer", proposal.cost_impact, proposal.raw)

        command_results = []
        for command in proposal.commands:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                command_results.append(
                    {
                        "command": command,
                        "decision": str(Decision.DENY),
                        "rule": None,
                        "reason": f"invalid command syntax: {exc}",
                    }
                )
                continue

            result = self.policy.evaluate(argv)
            command_result = {
                "command": command,
                "decision": str(result.decision),
                "rule": result.rule_id,
                "reason": result.reason,
            }
            command_results.append(command_result)
            self.audit.write(
                "team.command.proposed",
                actor="senior_developer",
                data={
                    "issue": issue.number,
                    "command": command,
                    "decision": result.decision,
                    "rule": result.rule_id,
                },
            )
            if result.decision == Decision.COST_APPROVAL:
                self._require_no_cost("command_policy", True, command_result)

        package = WorkPackage(
            repository=issue.repository,
            issue_number=issue.number,
            knowledge_repository=context.repository_name,
            knowledge_root=context.repository_root,
            engineering_brief=brief,
            technical_plan=plan,
            developer_proposal=proposal,
            command_policy_results=command_results,
        )
        saved = self.work_store.save_plan(package)
        self.audit.write(
            "team.plan.saved",
            actor="workflow",
            data={"issue": issue.number, "path": str(saved)},
        )
        return package, saved

    def review(self, work_package_path: Path) -> tuple[CodeReview, Path]:
        package = self.work_store.load(work_package_path)
        context = self.knowledge.build(
            str(package["knowledge_repository"]),
            repository_root=str(package["knowledge_root"]),
        )
        review = self.code_reviewer.review(package, context)
        issue_number = int(package["issue_number"])
        self._audit_stage("code_review", issue_number, review.raw)
        self._require_no_cost("code_review", review.cost_impact, review.raw)
        saved = self.work_store.save_review(issue_number, review)
        self.audit.write(
            "team.review.saved",
            actor="workflow",
            data={
                "issue": issue_number,
                "decision": review.decision,
                "path": str(saved),
            },
        )
        return review, saved

    def _audit_stage(self, actor: str, issue_number: int, payload: dict[str, Any]) -> None:
        self.audit.write(
            "team.handoff",
            actor=actor,
            data={"issue": issue_number, "output": payload},
        )

    def _require_no_cost(
        self, stage: str, cost_impact: bool, payload: dict[str, Any]
    ) -> None:
        if cost_impact:
            self.audit.write(
                "team.cost_gate",
                actor=stage,
                data={"status": "COST_APPROVAL_REQUIRED"},
            )
            raise CostApprovalRequired(stage, payload)
