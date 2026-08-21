from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..team.context import TeamKnowledgeContext
from .profiles import PROFILES, SpecialistRole, normalize_owner_role


class RoutingError(RuntimeError):
    pass


class DependencyCycleError(RoutingError):
    pass


class CrossRepositoryExecutionRequired(RoutingError):
    def __init__(self, repositories: set[str]) -> None:
        self.repositories = repositories
        super().__init__(
            "plan contains tasks for other repositories: " + ", ".join(sorted(repositories))
        )


@dataclass(frozen=True)
class AgentAssignment:
    task_id: str
    role: SpecialistRole
    repository: str
    files: tuple[str, ...]
    dependencies: tuple[str, ...]
    reason: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["role"] = self.role.value
        return data


class AgentRouter:
    """Deterministic specialist routing using Tech Lead intent plus repository evidence."""

    def route_plan(
        self,
        tasks: list[dict[str, Any]],
        *,
        context: TeamKnowledgeContext,
        active_repository: str,
    ) -> list[AgentAssignment]:
        assignments = [
            self.route_task(
                task,
                context=context,
                active_repository=active_repository,
            )
            for task in tasks
        ]
        return self._topological_order(assignments)

    def route_task(
        self,
        task: dict[str, Any],
        *,
        context: TeamKnowledgeContext,
        active_repository: str,
    ) -> AgentAssignment:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise RoutingError("technical task is missing id")
        files = tuple(str(item) for item in task.get("files", []))
        repository = str(task.get("repository") or active_repository)
        owner_role = str(task.get("owner_role") or "senior_developer")
        try:
            requested = normalize_owner_role(owner_role)
        except KeyError as exc:
            raise RoutingError(
                f"task {task_id} requests unsupported owner_role: {owner_role}"
            ) from exc
        signals = self._file_signals(files)
        if requested is None:
            role, reason = self._infer_role(signals, context)
            confidence = "inferred"
        else:
            self._validate_compatibility(task_id, requested, signals)
            role = requested
            reason = f"Tech Lead owner_role={owner_role}"
            confidence = "explicit"
        return AgentAssignment(
            task_id=task_id,
            role=role,
            repository=repository,
            files=files,
            dependencies=tuple(str(item) for item in task.get("dependencies", [])),
            reason=reason,
            confidence=confidence,
        )

    @staticmethod
    def external_repositories(
        assignments: list[AgentAssignment],
        *,
        active_repository: str,
    ) -> set[str]:
        return {
            item.repository
            for item in assignments
            if item.repository and item.repository != active_repository
        }

    def require_single_repository_execution(
        self,
        assignments: list[AgentAssignment],
        *,
        active_repository: str,
    ) -> None:
        external = self.external_repositories(
            assignments,
            active_repository=active_repository,
        )
        if external:
            raise CrossRepositoryExecutionRequired(external)

    @staticmethod
    def _file_signals(files: tuple[str, ...]) -> set[SpecialistRole]:
        signals: set[SpecialistRole] = set()
        for raw in files:
            value = raw.replace("\\", "/").lower()
            name = Path(value).name
            if (
                value.endswith((".kt", ".kts"))
                or "/android/" in value
                or "build.gradle" in name
                or "androidmanifest.xml" in name
            ):
                signals.add(SpecialistRole.MOBILE)
                continue
            if (
                value.endswith((".tsx", ".jsx"))
                or "/components/" in value
                or "/pages/" in value
                or "/app/" in value and value.endswith((".ts", ".tsx"))
            ):
                signals.add(SpecialistRole.FRONTEND)
                continue
            if (
                value.endswith(".sql")
                or "schema.prisma" in value
                or "/prisma/" in value
                or "/migrations/" in value
                or "/alembic/" in value
            ):
                signals.add(SpecialistRole.DATABASE)
                continue
            if (
                name.startswith("dockerfile")
                or "docker-compose" in name
                or "/.github/workflows/" in value
                or value.endswith((".tf", ".tfvars"))
                or "/terraform/" in value
                or "/k8s/" in value
                or "/kubernetes/" in value
                or "nginx" in value
            ):
                signals.add(SpecialistRole.DEVOPS_SRE)
                continue
            if (
                "/tests/" in value
                or "/test/" in value
                or value.startswith("tests/")
                or ".spec." in name
                or ".test." in name
                or name.startswith("test_")
            ):
                signals.add(SpecialistRole.QA_TESTING)
                continue
            if value.endswith((".py", ".ts", ".js")):
                signals.add(SpecialistRole.BACKEND)
        return signals

    def _infer_role(
        self,
        signals: set[SpecialistRole],
        context: TeamKnowledgeContext,
    ) -> tuple[SpecialistRole, str]:
        decisive = set(signals)
        if SpecialistRole.QA_TESTING in decisive and len(decisive) > 1:
            decisive.remove(SpecialistRole.QA_TESTING)
        if len(decisive) == 1:
            role = next(iter(decisive))
            return role, f"inferred from approved file paths ({role.value})"
        if len(decisive) > 1:
            raise RoutingError(
                "task spans multiple specialist domains; Tech Lead must split it: "
                + ", ".join(sorted(item.value for item in decisive))
            )
        stack_role = self._role_from_context(context)
        if stack_role is not None:
            return stack_role, f"inferred from Knowledge Engine stack ({stack_role.value})"
        if signals == {SpecialistRole.QA_TESTING}:
            return SpecialistRole.QA_TESTING, "test-only task"
        raise RoutingError("unable to infer specialist; Tech Lead must set owner_role")

    @staticmethod
    def _role_from_context(context: TeamKnowledgeContext) -> SpecialistRole | None:
        haystack = " ".join(
            [
                " ".join(str(key) for key in context.compact.get("languages", {})),
                " ".join(str(item) for item in context.compact.get("manifests", [])),
                " ".join(
                    f"{item.get('name', '')} {item.get('value', '')}"
                    for item in context.compact.get("facts", [])
                ),
            ]
        ).lower()
        if any(marker in haystack for marker in ("jetpack compose", "android", "kotlin")):
            return SpecialistRole.MOBILE
        if any(marker in haystack for marker in ("next.js", "nextjs", "react")):
            return SpecialistRole.FRONTEND
        if any(marker in haystack for marker in ("nestjs", "fastapi", "node.js", "python")):
            return SpecialistRole.BACKEND
        return None

    @staticmethod
    def _validate_compatibility(
        task_id: str,
        requested: SpecialistRole,
        signals: set[SpecialistRole],
    ) -> None:
        decisive = set(signals)
        if SpecialistRole.QA_TESTING in decisive and requested != SpecialistRole.QA_TESTING:
            decisive.remove(SpecialistRole.QA_TESTING)
        if not decisive or requested in decisive:
            return
        compatible_pairs = {
            frozenset({SpecialistRole.BACKEND, SpecialistRole.DATABASE}),
            frozenset({SpecialistRole.BACKEND, SpecialistRole.DEVOPS_SRE}),
        }
        if all(frozenset({requested, signal}) in compatible_pairs for signal in decisive):
            return
        raise RoutingError(
            f"task {task_id} owner_role={requested.value} is incompatible with file domain(s): "
            + ", ".join(sorted(item.value for item in decisive))
        )

    @staticmethod
    def _topological_order(assignments: list[AgentAssignment]) -> list[AgentAssignment]:
        by_id = {item.task_id: item for item in assignments}
        if len(by_id) != len(assignments):
            raise RoutingError("duplicate technical task ids")
        unknown = {
            dep
            for item in assignments
            for dep in item.dependencies
            if dep not in by_id
        }
        if unknown:
            raise RoutingError(
                "task dependency references unknown id(s): " + ", ".join(sorted(unknown))
            )
        remaining = {key: set(item.dependencies) for key, item in by_id.items()}
        ordered: list[AgentAssignment] = []
        emitted: set[str] = set()
        while len(ordered) < len(assignments):
            ready = sorted(
                task_id
                for task_id, deps in remaining.items()
                if task_id not in emitted and deps <= emitted
            )
            if not ready:
                unresolved = sorted(set(by_id) - emitted)
                raise DependencyCycleError(
                    "cyclic task dependencies: " + ", ".join(unresolved)
                )
            for task_id in ready:
                ordered.append(by_id[task_id])
                emitted.add(task_id)
        return ordered


def capabilities_payload() -> list[dict[str, Any]]:
    return [
        {
            "role": role.value,
            "display_name": PROFILES[role].display_name,
            "mission": PROFILES[role].mission,
            "stack": list(PROFILES[role].stack),
            "strengths": list(PROFILES[role].strengths),
        }
        for role in SpecialistRole
    ]
