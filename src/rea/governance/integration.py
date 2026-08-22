from __future__ import annotations

from pathlib import Path

from ..domain import IssueWorkUnit
from ..specialists.integration import SpecializedLevel6Workflow
from .workflow import AdvancedGovernanceWorkflow


class _PreplannedTeam:
    def __init__(self, package, saved: Path) -> None:
        self.package = package
        self.saved = saved

    def plan(self, *args, **kwargs):
        return self.package, self.saved


class GovernedLevel6Workflow(SpecializedLevel6Workflow):
    """Specialized Level 6 execution with a mandatory advanced-governance preflight."""

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
        self.governance = AdvancedGovernanceWorkflow(
            router=router,
            model=model,
            audit=audit,
        )

    def run(
        self,
        issue: IssueWorkUnit,
        *,
        source_workspace: Path,
        worktree_root: Path,
        knowledge_repository: str,
        knowledge_root: str | None,
        base_branch: str,
        sandbox_image: str,
        approved_rules: set[str],
        allow_network: bool = False,
        max_iterations: int = 3,
    ):
        package, saved = self.first_team.plan(
            issue,
            knowledge_repository=knowledge_repository,
            knowledge_root=knowledge_root,
        )
        context = self.knowledge.build(
            knowledge_repository,
            repository_root=knowledge_root,
        )
        assessment = self.governance.enforce(
            package.to_dict(),
            context,
            approvals=self._governance_approvals(approved_rules),
        )
        self.audit.write(
            "level6.governance_preflight",
            actor="advanced_governance",
            data={
                "issue": issue.number,
                "decision": assessment.decision.value,
                "risk_level": assessment.risk_level.value,
                "score": assessment.score,
            },
        )

        original_team = self.first_team
        self.first_team = _PreplannedTeam(package, saved)
        try:
            return super().run(
                issue,
                source_workspace=source_workspace,
                worktree_root=worktree_root,
                knowledge_repository=knowledge_repository,
                knowledge_root=knowledge_root,
                base_branch=base_branch,
                sandbox_image=sandbox_image,
                approved_rules=approved_rules,
                allow_network=allow_network,
                max_iterations=max_iterations,
            )
        finally:
            self.first_team = original_team

    @staticmethod
    def _governance_approvals(approved_rules: set[str]) -> set[str]:
        approvals: set[str] = set()
        if "governance-tech-lead" in approved_rules:
            approvals.add("tech-lead")
        if "governance-human" in approved_rules:
            approvals.add("human")
        return approvals
