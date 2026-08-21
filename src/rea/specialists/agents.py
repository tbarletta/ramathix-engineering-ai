from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..audit import AuditLog
from ..level6.coding import CODING_ITERATION_SCHEMA
from ..level6.contracts import CodingIteration, FileMutation
from ..models import ModelRouter
from ..team.agents import StructuredModelClient
from ..team.context import TeamKnowledgeContext
from ..team.contracts import CodeReview, ReviewDecision, ReviewFinding
from .profiles import PROFILES, SpecialistRole
from .router import AgentAssignment, RoutingError


QA_GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["approve", "request_changes"],
        },
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "missing_tests": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "decision",
        "summary",
        "findings",
        "missing_tests",
        "cost_impact",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class QAGateResult:
    decision: str
    summary: str
    findings: tuple[str, ...]
    missing_tests: tuple[str, ...]
    cost_impact: bool
    raw: dict[str, Any]


class SpecializedDeveloperAgent:
    def __init__(
        self,
        *,
        router: ModelRouter,
        model: StructuredModelClient,
        audit: AuditLog,
    ) -> None:
        self.router = router
        self.model = model
        self.audit = audit

    def implement(
        self,
        *,
        assignment: AgentAssignment,
        task: dict[str, Any],
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        source_files: dict[str, str],
        feedback: list[str],
        upstream_changes: list[dict[str, Any]],
    ) -> CodingIteration:
        profile = PROFILES[assignment.role]
        target = self.router.resolve(assignment.role.value)
        system = (
            f"You are the {profile.display_name} in Ramathix Engineering AI. "
            f"Mission: {profile.mission} "
            f"Primary stack: {', '.join(profile.stack)}. "
            "Implement only the assigned task and only paths supplied in source_files. "
            "For create/modify return complete UTF-8 content; for delete return null. "
            "Respect architecture, business rules, upstream task outputs and feedback. "
            "Commands must only validate/build/test. Never propose git push, deployment, "
            "cloud provisioning or destructive actions. Never include secrets. "
            "Mark cost_impact true if the implementation can create or increase monetary cost."
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=json.dumps(
                {
                    "assignment": assignment.to_dict(),
                    "task": task,
                    "work_package": work_package,
                    "knowledge": context.compact,
                    "source_files": source_files,
                    "upstream_changes": upstream_changes[-20:],
                    "feedback": feedback,
                },
                ensure_ascii=False,
                indent=2,
            ),
            schema=CODING_ITERATION_SCHEMA,
        )
        changes = [
            FileMutation(
                path=item["path"],
                action=item["action"],
                content=item["content"],
            )
            for item in data["changes"]
        ]
        allowed = set(assignment.files)
        outside = sorted({item.path for item in changes if item.path not in allowed})
        if outside:
            raise RoutingError(
                f"{assignment.role.value} attempted paths outside task {assignment.task_id}: "
                + ", ".join(outside)
            )
        self.audit.write(
            "specialist.handoff",
            actor=assignment.role.value,
            data={
                "task": assignment.task_id,
                "repository": assignment.repository,
                "files": list(assignment.files),
                "changes": [
                    {"path": item.path, "action": item.action}
                    for item in changes
                ],
                "commands": list(data["commands"]),
                "cost_impact": bool(data["cost_impact"]),
            },
        )
        return CodingIteration(
            summary=data["summary"],
            changes=changes,
            commands=list(data["commands"]),
            cost_impact=bool(data["cost_impact"]),
            raw={
                **data,
                "specialist_role": assignment.role.value,
                "task_id": assignment.task_id,
            },
        )


class QATestingGateAgent:
    role = SpecialistRole.QA_TESTING.value

    def __init__(
        self,
        *,
        router: ModelRouter,
        model: StructuredModelClient,
        audit: AuditLog,
    ) -> None:
        self.router = router
        self.model = model
        self.audit = audit

    def evaluate(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        assignments: list[AgentAssignment],
        diff: str,
        validation: list[dict[str, Any]],
    ) -> QAGateResult:
        target = self.router.resolve(self.role)
        profile = PROFILES[SpecialistRole.QA_TESTING]
        system = (
            f"You are the {profile.display_name}. "
            "Act as an independent quality gate before Code Review. "
            "Compare acceptance criteria, technical tasks, actual diff and validation results. "
            "Request changes if important acceptance paths, regressions or risk-based tests are "
            "missing. Do not require unrelated test expansion. Never approve hidden monetary cost."
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=json.dumps(
                {
                    "work_package": work_package,
                    "knowledge": context.compact,
                    "assignments": [item.to_dict() for item in assignments],
                    "diff": diff[:160_000],
                    "validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            ),
            schema=QA_GATE_SCHEMA,
        )
        result = QAGateResult(
            decision=data["decision"],
            summary=data["summary"],
            findings=tuple(data["findings"]),
            missing_tests=tuple(data["missing_tests"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )
        self.audit.write(
            "specialist.qa_gate",
            actor=self.role,
            data={
                "decision": result.decision,
                "findings": list(result.findings),
                "missing_tests": list(result.missing_tests),
                "cost_impact": result.cost_impact,
            },
        )
        return result


class SpecializedReviewCoordinator:
    """Runs QA gate first and independent source review second."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        model: StructuredModelClient,
        audit: AuditLog,
        code_reviewer,
        assignment_provider,
    ) -> None:
        self.qa = QATestingGateAgent(router=router, model=model, audit=audit)
        self.code_reviewer = code_reviewer
        self.assignment_provider = assignment_provider

    def review(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        diff: str,
        validation: list[dict[str, Any]],
    ) -> CodeReview:
        assignments = self.assignment_provider(work_package, context)
        qa = self.qa.evaluate(
            work_package=work_package,
            context=context,
            assignments=assignments,
            diff=diff,
            validation=validation,
        )
        if qa.decision == "request_changes":
            messages = [*qa.findings, *qa.missing_tests]
            return CodeReview(
                decision=ReviewDecision.REQUEST_CHANGES,
                summary=f"QA gate requested changes: {qa.summary}",
                findings=[
                    ReviewFinding(
                        severity="medium",
                        category="testing",
                        message=item,
                    )
                    for item in messages
                ],
                missing_tests=list(qa.missing_tests),
                risk_notes=[],
                cost_impact=qa.cost_impact,
                raw={
                    "decision": "request_changes",
                    "summary": qa.summary,
                    "findings": [
                        {
                            "severity": "medium",
                            "category": "testing",
                            "message": item,
                        }
                        for item in messages
                    ],
                    "missing_tests": list(qa.missing_tests),
                    "risk_notes": [],
                    "cost_impact": qa.cost_impact,
                    "qa_gate": qa.raw,
                },
            )
        review = self.code_reviewer.review(
            work_package=work_package,
            context=context,
            diff=diff,
            validation=validation,
        )
        review.cost_impact = review.cost_impact or qa.cost_impact
        review.raw["cost_impact"] = review.cost_impact
        review.raw["qa_gate"] = qa.raw
        return review
