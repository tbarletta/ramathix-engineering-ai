from __future__ import annotations

from typing import Any

from ..audit import AuditLog
from ..level6.coding import (
    MutationBoundaryError,
    SeniorDeveloperExecutionAgent,
    SourceCodeReviewerAgent,
)
from ..level6.contracts import CodingIteration, FileMutation
from ..level6.workflow import Level6Workflow as BaseLevel6Workflow
from ..team.context import TeamKnowledgeContext
from .agents import SpecializedDeveloperAgent, SpecializedReviewCoordinator
from .router import AgentAssignment, AgentRouter, RoutingError


class RoutedSeniorDeveloperExecutionAgent:
    def __init__(self, router, model, audit: AuditLog) -> None:
        self.router = router
        self.model = model
        self.audit = audit
        self.agent_router = AgentRouter()
        self.specialist = SpecializedDeveloperAgent(
            router=router,
            model=model,
            audit=audit,
        )
        self.fallback = SeniorDeveloperExecutionAgent(router, model)

    def assignments(
        self,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
    ) -> list[AgentAssignment]:
        tasks = list(work_package.get("technical_plan", {}).get("tasks", []))
        if not tasks:
            return []
        active_repository = str(work_package.get("repository") or context.repository_name)
        try:
            assignments = self.agent_router.route_plan(
                tasks,
                context=context,
                active_repository=active_repository,
            )
            self.agent_router.require_single_repository_execution(
                assignments,
                active_repository=active_repository,
            )
        except RoutingError as exc:
            raise MutationBoundaryError(f"specialist routing blocked: {exc}") from exc
        return assignments

    def implement(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        source_files: dict[str, str],
        feedback: list[str],
    ) -> CodingIteration:
        assignments = self.assignments(work_package, context)
        if not assignments:
            return self.fallback.implement(
                work_package=work_package,
                context=context,
                source_files=source_files,
                feedback=feedback,
            )
        tasks = {
            str(item.get("id")): item
            for item in work_package.get("technical_plan", {}).get("tasks", [])
        }
        changes: list[FileMutation] = []
        commands: list[str] = []
        summaries: list[str] = []
        outputs: list[dict[str, Any]] = []
        cost_impact = False

        for assignment in assignments:
            task = tasks[assignment.task_id]
            task_source = {
                path: source_files.get(path, "<NOT_IN_APPROVED_SOURCE_SNAPSHOT>")
                for path in assignment.files
            }
            upstream = [
                {"path": item.path, "action": item.action, "content": item.content}
                for item in changes
            ]
            coding = self.specialist.implement(
                assignment=assignment,
                task=task,
                work_package=work_package,
                context=context,
                source_files=task_source,
                feedback=feedback,
                upstream_changes=upstream,
            )
            summaries.append(f"{assignment.role.value}: {coding.summary}")
            changes.extend(coding.changes)
            for command in coding.commands:
                if command not in commands:
                    commands.append(command)
            cost_impact = cost_impact or coding.cost_impact
            outputs.append(
                {"assignment": assignment.to_dict(), "output": coding.raw}
            )

        summary = " | ".join(summaries)
        return CodingIteration(
            summary=summary,
            changes=changes,
            commands=commands,
            cost_impact=cost_impact,
            raw={
                "summary": summary,
                "changes": [
                    {"path": item.path, "action": item.action, "content": item.content}
                    for item in changes
                ],
                "commands": commands,
                "cost_impact": cost_impact,
                "specialist_assignments": [item.to_dict() for item in assignments],
                "specialist_outputs": outputs,
            },
        )


class SpecializedLevel6Workflow(BaseLevel6Workflow):
    def __init__(
        self,
        *,
        first_team,
        router,
        model,
        knowledge_store,
        work_store,
        policy,
        audit,
        github,
        artifact_store,
    ) -> None:
        super().__init__(
            first_team=first_team,
            router=router,
            model=model,
            knowledge_store=knowledge_store,
            work_store=work_store,
            policy=policy,
            audit=audit,
            github=github,
            artifact_store=artifact_store,
        )
        routed = RoutedSeniorDeveloperExecutionAgent(router, model, audit)
        self.coder = routed
        self.reviewer = SpecializedReviewCoordinator(
            router=router,
            model=model,
            audit=audit,
            code_reviewer=SourceCodeReviewerAgent(router, model),
            assignment_provider=routed.assignments,
        )
