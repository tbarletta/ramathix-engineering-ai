from __future__ import annotations

import json
from typing import Any, Protocol

from ..domain import IssueWorkUnit
from ..models import ModelRouter
from .contracts import (
    CodeReview,
    DeveloperProposal,
    EngineeringBrief,
    EngineeringTask,
    FileChangeProposal,
    ReviewDecision,
    ReviewFinding,
    TechnicalPlan,
)
from .context import TeamKnowledgeContext


class StructuredModelClient(Protocol):
    def chat_json(
        self, *, model: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]: ...


class RequestRejected(RuntimeError):
    pass


class CostApprovalRequired(RuntimeError):
    def __init__(self, stage: str, payload: dict[str, Any]) -> None:
        super().__init__(f"cost approval required at stage: {stage}")
        self.stage = stage
        self.payload = payload


ENGINEERING_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "business_context": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "impacted_repositories": {"type": "array", "items": {"type": "string"}},
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "objective",
        "business_context",
        "acceptance_criteria",
        "constraints",
        "impacted_repositories",
        "risk_level",
        "cost_impact",
    ],
    "additionalProperties": False,
}

TECHNICAL_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "architecture_summary": {"type": "string"},
        "decision": {"type": "string", "enum": ["approve", "reject"]},
        "rationale": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "owner_role": {"type": "string"},
                    "description": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "tests": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "id",
                    "owner_role",
                    "description",
                    "files",
                    "dependencies",
                    "acceptance_criteria",
                    "tests",
                ],
                "additionalProperties": False,
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "rollback_strategy": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "architecture_summary",
        "decision",
        "rationale",
        "tasks",
        "risks",
        "rollback_strategy",
        "cost_impact",
    ],
    "additionalProperties": False,
}

DEVELOPER_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["create", "modify", "delete"],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["path", "action", "rationale"],
                "additionalProperties": False,
            },
        },
        "implementation_steps": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "commands": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "summary",
        "files",
        "implementation_steps",
        "tests",
        "assumptions",
        "commands",
        "cost_impact",
    ],
    "additionalProperties": False,
}

CODE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["approve", "request_changes"],
        },
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "critical"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "architecture",
                            "business_rule",
                            "security",
                            "performance",
                            "database",
                            "testing",
                            "operations",
                            "maintainability",
                        ],
                    },
                    "message": {"type": "string"},
                },
                "required": ["severity", "category", "message"],
                "additionalProperties": False,
            },
        },
        "missing_tests": {"type": "array", "items": {"type": "string"}},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "decision",
        "summary",
        "findings",
        "missing_tests",
        "risk_notes",
        "cost_impact",
    ],
    "additionalProperties": False,
}


def _issue_payload(issue: IssueWorkUnit) -> dict[str, Any]:
    return {
        "repository": issue.repository,
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "labels": list(issue.labels),
        "url": issue.url,
    }


class EngineeringManagerAgent:
    role = "engineering_manager"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def create_brief(
        self, issue: IssueWorkUnit, context: TeamKnowledgeContext
    ) -> EngineeringBrief:
        target = self.router.resolve(self.role)
        system = (
            "You are the Engineering Manager of Ramathix Engineering AI. Convert a GitHub "
            "Issue into an executable engineering brief. Preserve business intent, identify "
            "acceptance criteria, constraints, risk and repository impact. Use only supplied "
            "knowledge as factual context. Mark cost impact true for any change that may create "
            "or increase monetary cost. Do not invent permanent decisions."
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=_prompt(issue, context),
            schema=ENGINEERING_BRIEF_SCHEMA,
        )
        return EngineeringBrief(
            objective=data["objective"],
            business_context=data["business_context"],
            acceptance_criteria=list(data["acceptance_criteria"]),
            constraints=list(data["constraints"]),
            impacted_repositories=list(data["impacted_repositories"]),
            risk_level=data["risk_level"],
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )


class TechLeadTeamAgent:
    role = "tech_lead"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def plan(
        self,
        issue: IssueWorkUnit,
        brief: EngineeringBrief,
        context: TeamKnowledgeContext,
    ) -> TechnicalPlan:
        target = self.router.resolve(self.role)
        system = (
            "You are the Tech Lead. Validate the request against architecture, business rules "
            "and risk. You may reject an unsafe or architecturally invalid approach and should "
            "then describe the alternative in the rationale. Decompose approved work into "
            "small testable tasks. Respect confirmed/documented knowledge over inference. "
            "Never silently introduce a cost-bearing decision."
        )
        user = json.dumps(
            {
                "issue": _issue_payload(issue),
                "engineering_brief": brief.raw,
                "knowledge": context.compact,
            },
            ensure_ascii=False,
            indent=2,
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=TECHNICAL_PLAN_SCHEMA,
        )
        tasks = [
            EngineeringTask(
                id=item["id"],
                owner_role=item["owner_role"],
                description=item["description"],
                files=list(item["files"]),
                dependencies=list(item["dependencies"]),
                acceptance_criteria=list(item["acceptance_criteria"]),
                tests=list(item["tests"]),
            )
            for item in data["tasks"]
        ]
        return TechnicalPlan(
            architecture_summary=data["architecture_summary"],
            decision=data["decision"],
            rationale=data["rationale"],
            tasks=tasks,
            risks=list(data["risks"]),
            rollback_strategy=list(data["rollback_strategy"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )


class SeniorDeveloperAgent:
    role = "senior_developer"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def propose(
        self,
        issue: IssueWorkUnit,
        brief: EngineeringBrief,
        plan: TechnicalPlan,
        context: TeamKnowledgeContext,
    ) -> DeveloperProposal:
        if plan.decision != "approve":
            raise ValueError("Tech Lead rejected the request; implementation proposal is blocked")
        target = self.router.resolve(self.role)
        system = (
            "You are a Senior Developer. Produce an implementation proposal, not code changes. "
            "Specify affected files, ordered implementation steps, tests and commands that would "
            "be needed. Commands are proposals only and will later be checked by the deterministic "
            "policy engine. Do not assume files or APIs absent from supplied knowledge. Highlight "
            "assumptions. Never bypass architecture or business constraints."
        )
        user = json.dumps(
            {
                "issue": _issue_payload(issue),
                "engineering_brief": brief.raw,
                "technical_plan": plan.raw,
                "knowledge": context.compact,
            },
            ensure_ascii=False,
            indent=2,
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=DEVELOPER_PROPOSAL_SCHEMA,
        )
        return DeveloperProposal(
            summary=data["summary"],
            files=[
                FileChangeProposal(
                    path=item["path"],
                    action=item["action"],
                    rationale=item["rationale"],
                )
                for item in data["files"]
            ],
            implementation_steps=list(data["implementation_steps"]),
            tests=list(data["tests"]),
            assumptions=list(data["assumptions"]),
            commands=list(data["commands"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )


class CodeReviewerAgent:
    role = "code_review"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def review(
        self, work_package: dict[str, Any], context: TeamKnowledgeContext
    ) -> CodeReview:
        target = self.router.resolve(self.role)
        system = (
            "You are an independent Senior Code Reviewer. Review the proposed implementation "
            "against architecture, business constraints, security, performance, database safety, "
            "testing and operational risk. This V0.3 review is plan-level; no source diff exists "
            "yet. Request changes for material gaps. Never approve a proposal that silently "
            "introduces monetary cost or contradicts higher-priority knowledge."
        )
        user = json.dumps(
            {"work_package": work_package, "knowledge": context.compact},
            ensure_ascii=False,
            indent=2,
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=CODE_REVIEW_SCHEMA,
        )
        return CodeReview(
            decision=ReviewDecision(data["decision"]),
            summary=data["summary"],
            findings=[
                ReviewFinding(
                    severity=item["severity"],
                    category=item["category"],
                    message=item["message"],
                )
                for item in data["findings"]
            ],
            missing_tests=list(data["missing_tests"]),
            risk_notes=list(data["risk_notes"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )


def _prompt(issue: IssueWorkUnit, context: TeamKnowledgeContext) -> str:
    return json.dumps(
        {"issue": _issue_payload(issue), "knowledge": context.compact},
        ensure_ascii=False,
        indent=2,
    )
