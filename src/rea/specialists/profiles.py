from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class SpecialistRole(StrEnum):
    BACKEND = "senior_backend"
    FRONTEND = "senior_frontend"
    MOBILE = "senior_mobile"
    DATABASE = "database"
    DEVOPS_SRE = "devops_sre"
    QA_TESTING = "qa_testing"


@dataclass(frozen=True)
class AgentProfile:
    role: SpecialistRole
    display_name: str
    mission: str
    stack: tuple[str, ...]
    strengths: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[SpecialistRole, AgentProfile] = {
    SpecialistRole.BACKEND: AgentProfile(
        role=SpecialistRole.BACKEND,
        display_name="Senior Backend Agent",
        mission=(
            "Implement APIs, services, workers and domain logic without violating architecture."
        ),
        stack=(
            "TypeScript",
            "Node.js",
            "NestJS",
            "Python",
            "FastAPI",
            "BullMQ",
            "Celery",
            "RabbitMQ",
        ),
        strengths=(
            "distributed systems",
            "API design",
            "domain logic",
            "async processing",
            "integration",
        ),
    ),
    SpecialistRole.FRONTEND: AgentProfile(
        role=SpecialistRole.FRONTEND,
        display_name="Senior Frontend Agent",
        mission="Implement maintainable web interfaces and client-side behavior.",
        stack=("TypeScript", "React", "Next.js", "Vitest", "Testing Library"),
        strengths=("UI architecture", "state", "accessibility", "performance", "frontend testing"),
    ),
    SpecialistRole.MOBILE: AgentProfile(
        role=SpecialistRole.MOBILE,
        display_name="Senior Mobile Agent",
        mission="Implement Android-native features consistent with Ramathix mobile architecture.",
        stack=("Kotlin", "Android", "Jetpack Compose", "Room", "DataStore", "Gradle"),
        strengths=("mobile architecture", "Compose UI", "offline data", "Android lifecycle"),
    ),
    SpecialistRole.DATABASE: AgentProfile(
        role=SpecialistRole.DATABASE,
        display_name="Database Agent",
        mission="Own schemas, migrations, persistence safety and data-access correctness.",
        stack=("PostgreSQL", "pgvector", "Prisma", "SQLAlchemy", "Alembic", "Redis"),
        strengths=("schema design", "migrations", "query performance", "data integrity"),
    ),
    SpecialistRole.DEVOPS_SRE: AgentProfile(
        role=SpecialistRole.DEVOPS_SRE,
        display_name="DevOps/SRE Agent",
        mission=(
            "Own build, runtime, deployment and observability changes within policy boundaries."
        ),
        stack=(
            "Docker",
            "Docker Compose",
            "GitHub Actions",
            "Nginx",
            "RabbitMQ",
            "MinIO",
            "AWS S3",
            "OpenTelemetry",
            "Prometheus",
        ),
        strengths=("CI/CD", "containers", "runtime reliability", "observability", "operations"),
    ),
    SpecialistRole.QA_TESTING: AgentProfile(
        role=SpecialistRole.QA_TESTING,
        display_name="QA/Testing Agent",
        mission="Implement and assess automated verification against acceptance criteria and risk.",
        stack=("pytest", "Jest", "Vitest", "Testing Library", "Gradle"),
        strengths=("test strategy", "regression", "edge cases", "quality gates"),
    ),
}


_ALIASES: dict[str, SpecialistRole | None] = {
    "senior_backend": SpecialistRole.BACKEND,
    "backend": SpecialistRole.BACKEND,
    "backend_agent": SpecialistRole.BACKEND,
    "backend_developer": SpecialistRole.BACKEND,
    "senior_frontend": SpecialistRole.FRONTEND,
    "frontend": SpecialistRole.FRONTEND,
    "frontend_agent": SpecialistRole.FRONTEND,
    "frontend_developer": SpecialistRole.FRONTEND,
    "senior_mobile": SpecialistRole.MOBILE,
    "mobile": SpecialistRole.MOBILE,
    "android": SpecialistRole.MOBILE,
    "mobile_agent": SpecialistRole.MOBILE,
    "database": SpecialistRole.DATABASE,
    "database_agent": SpecialistRole.DATABASE,
    "db": SpecialistRole.DATABASE,
    "dba": SpecialistRole.DATABASE,
    "devops_sre": SpecialistRole.DEVOPS_SRE,
    "devops": SpecialistRole.DEVOPS_SRE,
    "sre": SpecialistRole.DEVOPS_SRE,
    "cloud_devops": SpecialistRole.DEVOPS_SRE,
    "qa_testing": SpecialistRole.QA_TESTING,
    "qa": SpecialistRole.QA_TESTING,
    "testing": SpecialistRole.QA_TESTING,
    "test": SpecialistRole.QA_TESTING,
    "senior_developer": None,
    "developer": None,
    "software_engineer": None,
}


def normalize_owner_role(value: str) -> SpecialistRole | None:
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _ALIASES:
        raise KeyError(value)
    return _ALIASES[key]


def capabilities() -> list[dict]:
    return [PROFILES[role].to_dict() for role in SpecialistRole]
