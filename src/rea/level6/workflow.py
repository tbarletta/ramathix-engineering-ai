from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from ..audit import AuditLog
from ..domain import Decision, IssueWorkUnit
from ..execution import ExecutionApprovalRequired, ExecutionDenied, GovernedLocalRunner
from ..knowledge.store import JsonKnowledgeStore
from ..policy import CommandPolicy
from ..sandbox import DockerSandbox, PolicyViolation
from ..team import CostApprovalRequired, FirstTeamWorkflow, WorkPackageStore
from ..team.context import KnowledgeContextBuilder
from ..team.contracts import WorkPackage
from .coding import (
    SeniorDeveloperExecutionAgent,
    SourceCodeReviewerAgent,
    WorkspaceMutator,
    WorkspaceReader,
)
from .contracts import IterationRecord, Level6Result, ValidationCommand
from .workspace import WorkspaceManager, Worktree


class PullRequestClient(Protocol):
    def ensure_pull_request(
        self,
        repository: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, Any]: ...


class IterationLimitExceeded(RuntimeError):
    pass


class Level6ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, result: Level6Result) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"issue-{result.issue_number}-execution.json"
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path


class Level6Workflow:
    def __init__(
        self,
        *,
        first_team: FirstTeamWorkflow,
        router,
        model,
        knowledge_store: JsonKnowledgeStore,
        work_store: WorkPackageStore,
        policy: CommandPolicy,
        audit: AuditLog,
        github: PullRequestClient,
        artifact_store: Level6ArtifactStore,
    ) -> None:
        self.first_team = first_team
        self.knowledge = KnowledgeContextBuilder(knowledge_store)
        self.work_store = work_store
        self.policy = policy
        self.audit = audit
        self.github = github
        self.artifact_store = artifact_store
        self.coder = SeniorDeveloperExecutionAgent(router, model)
        self.reviewer = SourceCodeReviewerAgent(router, model)

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
        on_progress: Callable[[str], None] | None = None,
    ) -> Level6Result:
        if max_iterations < 1 or max_iterations > 8:
            raise ValueError("max_iterations must be between 1 and 8")
        report = on_progress or (lambda _label: None)

        report("Planejando o pacote de trabalho...")
        package, _ = self.first_team.plan(
            issue,
            knowledge_repository=knowledge_repository,
            knowledge_root=knowledge_root,
        )
        package_dict = package.to_dict()
        context = self.knowledge.build(
            knowledge_repository,
            repository_root=knowledge_root,
        )
        self._require_publish_approval(approved_rules)

        local_runner = GovernedLocalRunner(self.policy, self.audit)
        manager = WorkspaceManager(
            source_root=source_workspace,
            worktree_root=worktree_root,
            runner=local_runner,
            audit=self.audit,
        )
        report("Preparando o ambiente de trabalho (worktree)...")
        worktree = manager.prepare(
            issue_number=issue.number,
            issue_title=issue.title,
            base_branch=base_branch,
        )
        allowed_paths = self._allowed_paths(package)
        if not allowed_paths:
            raise RuntimeError("technical plan did not approve any file paths")

        result = Level6Result(
            issue_number=issue.number,
            branch=worktree.branch,
            pull_request_url=None,
        )
        feedback: list[str] = []
        sandbox = DockerSandbox(self.policy, self.audit)

        for iteration_number in range(1, max_iterations + 1):
            report(f"Implementando a solução (iteração {iteration_number}/{max_iterations})...")
            reader = WorkspaceReader(worktree.path)
            source_files = reader.snapshot(allowed_paths)
            coding = self.coder.implement(
                work_package=package_dict,
                context=context,
                source_files=source_files,
                feedback=feedback,
            )
            self._audit_coding(issue.number, iteration_number, coding)
            if coding.cost_impact:
                raise CostApprovalRequired("level6_coding", coding.raw)

            mutator = WorkspaceMutator(worktree.path, allowed_paths=allowed_paths)
            for mutation in coding.changes:
                mutator.apply(mutation)
                self.audit.write(
                    "level6.file_mutated",
                    actor="senior_developer",
                    data={
                        "issue": issue.number,
                        "iteration": iteration_number,
                        "path": mutation.path,
                        "action": mutation.action,
                    },
                )

            report(f"Validando (rodando testes, iteração {iteration_number})...")
            validation = self._validate(
                sandbox=sandbox,
                worktree=worktree,
                commands=coding.commands,
                sandbox_image=sandbox_image,
                approved_rules=approved_rules,
                allow_network=allow_network,
            )
            manager.assert_only_allowed_changes(worktree, allowed_paths)
            failed = [item for item in validation if item.returncode != 0]
            if failed:
                record = IterationRecord(
                    number=iteration_number,
                    coding=coding,
                    validation=validation,
                )
                result.iterations.append(record)
                self.artifact_store.save(result)
                feedback = self._validation_feedback(failed)
                continue

            manager.stage(worktree)
            diff = manager.diff(worktree, staged=True)
            if not diff.strip():
                record = IterationRecord(
                    number=iteration_number,
                    coding=coding,
                    validation=validation,
                    diff=diff,
                )
                result.iterations.append(record)
                self.artifact_store.save(result)
                feedback = [
                    "No staged source diff was produced. Apply the approved implementation."
                ]
                continue

            report(f"Revisando o código (iteração {iteration_number})...")
            review = self.reviewer.review(
                work_package=package_dict,
                context=context,
                diff=diff,
                validation=[asdict(item) for item in validation],
            )
            self.audit.write(
                "level6.review",
                actor="code_review",
                data={
                    "issue": issue.number,
                    "iteration": iteration_number,
                    "decision": review.decision,
                    "findings": [asdict(item) for item in review.findings],
                },
            )
            if review.cost_impact:
                raise CostApprovalRequired("level6_code_review", review.raw)

            record = IterationRecord(
                number=iteration_number,
                coding=coding,
                validation=validation,
                diff=diff,
                review=review.raw,
            )
            result.iterations.append(record)
            self.artifact_store.save(result)

            if review.decision == "request_changes":
                feedback = [
                    f"{item.severity}/{item.category}: {item.message}"
                    for item in review.findings
                ]
                feedback.extend(f"Missing test: {item}" for item in review.missing_tests)
                continue

            report("Criando commit e enviando a branch...")
            commit_sha = manager.commit(
                worktree,
                message=f"feat: address issue #{issue.number}",
            )
            manager.push(worktree, approved_rules=approved_rules)
            report("Abrindo Pull Request...")
            pr = self.github.ensure_pull_request(
                issue.repository,
                title=f"REA #{issue.number}: {issue.title}",
                body=self._pull_request_body(issue, package, result, commit_sha),
                head=worktree.branch,
                base=base_branch,
                draft=True,
            )
            result.pull_request_url = str(pr.get("html_url") or pr.get("url") or "")
            result.status = "draft_pr_created"
            saved = self.artifact_store.save(result)
            self.audit.write(
                "level6.completed",
                actor="issue_to_pr",
                data={
                    "issue": issue.number,
                    "branch": worktree.branch,
                    "commit": commit_sha,
                    "pull_request": result.pull_request_url,
                    "artifact": str(saved),
                },
            )
            manager.cleanup(worktree)
            return result

        result.status = "iteration_limit_exceeded"
        self.artifact_store.save(result)
        self.audit.write(
            "level6.blocked",
            actor="issue_to_pr",
            data={"issue": issue.number, "reason": result.status},
        )
        raise IterationLimitExceeded(
            f"issue #{issue.number} exceeded {max_iterations} implementation iterations"
        )

    def _validate(
        self,
        *,
        sandbox: DockerSandbox,
        worktree: Worktree,
        commands: list[str],
        sandbox_image: str,
        approved_rules: set[str],
        allow_network: bool,
    ) -> list[ValidationCommand]:
        results: list[ValidationCommand] = []
        for command in commands:
            argv = shlex.split(command)
            policy_result = self.policy.evaluate(argv)
            if policy_result.decision is Decision.COST_APPROVAL:
                raise CostApprovalRequired(
                    "level6_validation_command",
                    {"command": command, "rule": policy_result.rule_id},
                )
            allowed_validation_rules = {"tests-and-quality", "dependency-install"}
            if policy_result.rule_id not in allowed_validation_rules:
                raise PolicyViolation(
                    f"command is outside V0.4 validation scope: {command}"
                )
            if policy_result.rule_id == "dependency-install" and not allow_network:
                raise PolicyViolation(
                    "dependency installation requires explicit --allow-network"
                )
            command_network = (
                allow_network and policy_result.rule_id == "dependency-install"
            )
            execution = sandbox.run(
                image=sandbox_image,
                workspace=worktree.path,
                argv=argv,
                network=command_network,
                approved_rules=approved_rules,
            )
            results.append(
                ValidationCommand(
                    command=command,
                    returncode=execution.returncode,
                    stdout=execution.stdout[-20_000:],
                    stderr=execution.stderr[-20_000:],
                )
            )
        return results

    def _require_publish_approval(self, approved_rules: set[str]) -> None:
        argv = ["git", "push"]
        result = self.policy.evaluate(argv)
        if result.decision is Decision.COST_APPROVAL:
            raise CostApprovalRequired(
                "git_push_policy",
                {"command": "git push", "rule": result.rule_id},
            )
        if result.decision is Decision.DENY:
            raise ExecutionDenied(result.reason)
        if result.decision is Decision.ASK and result.rule_id not in approved_rules:
            raise ExecutionApprovalRequired(
                decision=result.decision,
                rule_id=result.rule_id,
                argv=argv,
                reason=result.reason,
            )

    @staticmethod
    def _allowed_paths(package: WorkPackage) -> set[str]:
        paths = {item.path for item in package.developer_proposal.files}
        for task in package.technical_plan.tasks:
            paths.update(task.files)
        normalized: set[str] = set()
        for value in paths:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe path in approved technical plan: {value}")
            normalized.add(path.as_posix())
        return normalized

    @staticmethod
    def _validation_feedback(failed: list[ValidationCommand]) -> list[str]:
        return [
            (
                f"Validation failed: {item.command}\n"
                f"stdout:\n{item.stdout[-8_000:]}\n"
                f"stderr:\n{item.stderr[-8_000:]}"
            )
            for item in failed
        ]

    def _audit_coding(self, issue: int, iteration: int, coding) -> None:
        self.audit.write(
            "level6.coding_iteration",
            actor="senior_developer",
            data={
                "issue": issue,
                "iteration": iteration,
                "summary": coding.summary,
                "changes": [
                    {"path": item.path, "action": item.action}
                    for item in coding.changes
                ],
                "commands": coding.commands,
                "cost_impact": coding.cost_impact,
            },
        )

    @staticmethod
    def _pull_request_body(
        issue: IssueWorkUnit,
        package: WorkPackage,
        result: Level6Result,
        commit_sha: str,
    ) -> str:
        last = result.iterations[-1]
        validation = "\n".join(
            f"- `{item.command}`: {'PASS' if item.returncode == 0 else 'FAIL'}"
            for item in last.validation
        ) or "- No validation command was required."
        return (
            f"## REA Level 6\n\nCloses #{issue.number}\n\n"
            f"### Objective\n{package.engineering_brief.objective}\n\n"
            f"### Technical approach\n{package.technical_plan.architecture_summary}\n\n"
            f"### Validation\n{validation}\n\n"
            f"### Independent review\n{last.review.get('summary', '') if last.review else ''}\n\n"
            f"### Commit\n`{commit_sha}`\n\n"
            "This pull request was created as **draft** by Ramathix Engineering AI. "
            "Merge remains outside the V0.4 autonomous scope."
        )
