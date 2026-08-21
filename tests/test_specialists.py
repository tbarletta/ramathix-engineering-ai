import pytest

from rea.specialists.profiles import SpecialistRole
from rea.specialists.router import (
    AgentRouter,
    CrossRepositoryExecutionRequired,
    DependencyCycleError,
    RoutingError,
)
from rea.team.context import TeamKnowledgeContext


def context(*facts: str) -> TeamKnowledgeContext:
    return TeamKnowledgeContext(
        repository_name="example",
        repository_root="/workspace/example",
        inventory={},
        compact={
            "repository": "example",
            "languages": {"TypeScript": 10},
            "manifests": ["package.json"],
            "facts": [
                {"name": item, "value": item, "confidence": "confirmed"}
                for item in facts
            ],
        },
    )


def test_router_uses_explicit_backend_role() -> None:
    assignment = AgentRouter().route_task(
        {
            "id": "backend-1",
            "owner_role": "senior_backend",
            "files": ["src/orders/orders.service.ts"],
            "dependencies": [],
        },
        context=context(),
        active_repository="tbarletta/example",
    )
    assert assignment.role is SpecialistRole.BACKEND
    assert assignment.confidence == "explicit"


def test_router_rejects_incompatible_role() -> None:
    with pytest.raises(RoutingError):
        AgentRouter().route_task(
            {
                "id": "bad-mobile",
                "owner_role": "senior_backend",
                "files": ["app/src/main/java/com/ramathix/MainActivity.kt"],
                "dependencies": [],
            },
            context=context(),
            active_repository="tbarletta/example",
        )


def test_router_requires_tech_lead_split_for_database_file() -> None:
    with pytest.raises(RoutingError, match="Tech Lead must split"):
        AgentRouter().route_task(
            {
                "id": "mixed-backend-db",
                "owner_role": "senior_backend",
                "files": ["src/orders/orders.service.ts", "prisma/schema.prisma"],
                "dependencies": [],
            },
            context=context(),
            active_repository="tbarletta/example",
        )


def test_router_infers_frontend_for_generic_task() -> None:
    assignment = AgentRouter().route_task(
        {
            "id": "ui-1",
            "owner_role": "senior_developer",
            "files": ["src/components/PostCard.tsx"],
            "dependencies": [],
        },
        context=context(),
        active_repository="tbarletta/example",
    )
    assert assignment.role is SpecialistRole.FRONTEND
    assert assignment.confidence == "inferred"


def test_router_infers_frontend_typescript_inside_web_app() -> None:
    assignment = AgentRouter().route_task(
        {
            "id": "web-lib",
            "owner_role": "senior_developer",
            "files": ["apps/web/src/lib/api-client.ts"],
            "dependencies": [],
        },
        context=context("React"),
        active_repository="tbarletta/example",
    )
    assert assignment.role is SpecialistRole.FRONTEND


def test_router_does_not_guess_when_repository_stack_is_ambiguous() -> None:
    ambiguous = TeamKnowledgeContext(
        repository_name="example",
        repository_root="/workspace/example",
        inventory={},
        compact={
            "repository": "example",
            "languages": {"TypeScript": 10},
            "manifests": ["package.json"],
            "facts": [
                {"name": "React", "value": "React", "confidence": "confirmed"},
                {"name": "NestJS", "value": "NestJS", "confidence": "confirmed"},
            ],
        },
    )
    with pytest.raises(RoutingError, match="unable to infer specialist"):
        AgentRouter().route_task(
            {
                "id": "ambiguous",
                "owner_role": "senior_developer",
                "files": [],
                "dependencies": [],
            },
            context=ambiguous,
            active_repository="tbarletta/example",
        )


def test_router_respects_dependencies() -> None:
    tasks = [
        {
            "id": "b",
            "owner_role": "senior_backend",
            "files": ["src/b.service.ts"],
            "dependencies": ["a"],
        },
        {
            "id": "a",
            "owner_role": "database",
            "files": ["prisma/schema.prisma"],
            "dependencies": [],
        },
    ]
    routed = AgentRouter().route_plan(
        tasks,
        context=context(),
        active_repository="tbarletta/example",
    )
    assert [item.task_id for item in routed] == ["a", "b"]


def test_router_blocks_dependency_cycle() -> None:
    tasks = [
        {
            "id": "a",
            "owner_role": "senior_backend",
            "files": ["src/a.ts"],
            "dependencies": ["b"],
        },
        {
            "id": "b",
            "owner_role": "senior_backend",
            "files": ["src/b.ts"],
            "dependencies": ["a"],
        },
    ]
    with pytest.raises(DependencyCycleError):
        AgentRouter().route_plan(
            tasks,
            context=context(),
            active_repository="tbarletta/example",
        )


def test_router_detects_external_repository() -> None:
    router = AgentRouter()
    assignments = router.route_plan(
        [
            {
                "id": "api",
                "owner_role": "senior_backend",
                "repository": "tbarletta/api",
                "files": ["src/api.py"],
                "dependencies": [],
            }
        ],
        context=context(),
        active_repository="tbarletta/web",
    )
    with pytest.raises(CrossRepositoryExecutionRequired):
        router.require_single_repository_execution(
            assignments,
            active_repository="tbarletta/web",
        )
