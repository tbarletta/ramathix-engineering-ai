from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import Confidence, DependencyEdge, Evidence, EvidenceKind, KnowledgeFact


@dataclass
class ScanContext:
    root: Path
    files: list[Path]
    max_read_bytes: int
    _cache: dict[Path, str] = field(default_factory=dict)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def text(self, path: Path) -> str:
        if path in self._cache:
            return self._cache[path]
        try:
            if path.stat().st_size > self.max_read_bytes:
                return ""
            value = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            value = ""
        self._cache[path] = value
        return value

    def named(self, *names: str) -> list[Path]:
        wanted = set(names)
        return [path for path in self.files if path.name in wanted]


@dataclass
class AnalysisResult:
    facts: list[KnowledgeFact] = field(default_factory=list)
    dependencies: list[DependencyEdge] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: AnalysisResult) -> None:
        self.facts.extend(other.facts)
        self.dependencies.extend(other.dependencies)
        self.manifests.extend(other.manifests)
        self.warnings.extend(other.warnings)


class RepositoryAnalyzer(Protocol):
    def analyze(self, context: ScanContext) -> AnalysisResult: ...


def _fact(
    category: str,
    name: str,
    value: str,
    path: str,
    *,
    detail: str | None = None,
    kind: EvidenceKind = EvidenceKind.MANIFEST,
) -> KnowledgeFact:
    return KnowledgeFact(
        category=category,
        name=name,
        value=value,
        confidence=Confidence.CONFIRMED,
        evidence=Evidence(path, kind, detail),
    )


class ManifestAnalyzer:
    MANIFESTS = {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "pom.xml",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "composer.json",
        "Podfile",
        "Package.swift",
    }

    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        for path in context.files:
            if path.name in self.MANIFESTS:
                relative = context.relative(path)
                result.manifests.append(relative)
                result.facts.append(_fact("manifest", path.name, relative, relative))
        return result


class FrameworkAnalyzer:
    NODE_FRAMEWORKS = {
        "@nestjs/core": "NestJS",
        "next": "Next.js",
        "react": "React",
        "@prisma/client": "Prisma",
        "prisma": "Prisma",
        "bullmq": "BullMQ",
        "vitest": "Vitest",
        "jest": "Jest",
        "typescript": "TypeScript",
    }
    PYTHON_FRAMEWORKS = {
        "fastapi": "FastAPI",
        "sqlalchemy": "SQLAlchemy",
        "alembic": "Alembic",
        "celery": "Celery",
        "redis": "Redis",
        "pgvector": "pgvector",
        "pydantic": "Pydantic",
        "pytest": "pytest",
        "typer": "Typer",
    }

    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        seen: set[tuple[str, str]] = set()
        for path in context.named("package.json"):
            relative = context.relative(path)
            try:
                payload = json.loads(context.text(path))
            except json.JSONDecodeError as exc:
                result.warnings.append(f"Invalid {relative}: {exc}")
                continue
            dependencies: dict[str, str] = {}
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                dependencies.update(payload.get(section) or {})
            for dependency, framework in self.NODE_FRAMEWORKS.items():
                if dependency in dependencies:
                    key = ("framework", framework)
                    if key not in seen:
                        result.facts.append(
                            _fact("framework", framework, dependencies[dependency], relative)
                        )
                        seen.add(key)

        for path in context.named("pyproject.toml"):
            relative = context.relative(path)
            try:
                payload = tomllib.loads(context.text(path))
            except tomllib.TOMLDecodeError as exc:
                result.warnings.append(f"Invalid {relative}: {exc}")
                continue
            dependencies = payload.get("project", {}).get("dependencies", [])
            normalized = "\n".join(str(item).lower() for item in dependencies)
            for dependency, framework in self.PYTHON_FRAMEWORKS.items():
                if re.search(rf"(^|\n){re.escape(dependency)}(?:\[|[<>=!~ ]|$)", normalized):
                    key = ("framework", framework)
                    if key not in seen:
                        result.facts.append(_fact("framework", framework, "present", relative))
                        seen.add(key)

        for path in context.named("requirements.txt"):
            relative = context.relative(path)
            normalized = context.text(path).lower()
            for dependency, framework in self.PYTHON_FRAMEWORKS.items():
                if re.search(rf"(?m)^{re.escape(dependency)}(?:\[|[<>=!~ ]|$)", normalized):
                    key = ("framework", framework)
                    if key not in seen:
                        result.facts.append(_fact("framework", framework, "present", relative))
                        seen.add(key)

        for path in context.named("build.gradle.kts", "build.gradle"):
            relative = context.relative(path)
            text = context.text(path)
            android_markers = {
                "com.android.application": "Android",
                "androidx.compose": "Jetpack Compose",
                "androidx.room": "Room",
                "com.google.ai.edge.litertlm": "LiteRT-LM",
            }
            for marker, framework in android_markers.items():
                if marker in text:
                    key = ("framework", framework)
                    if key not in seen:
                        result.facts.append(_fact("framework", framework, "present", relative))
                        seen.add(key)
        return result


class DataAnalyzer:
    MARKERS = {
        "postgresql": "PostgreSQL",
        "postgres:": "PostgreSQL",
        "pgvector": "pgvector",
        "redis": "Redis",
        "minio": "MinIO",
        "sqlite": "SQLite",
        "room-runtime": "Room",
    }

    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        candidates = [
            path
            for path in context.files
            if path.name in {"package.json", "pyproject.toml", "requirements.txt"}
            or path.name.startswith("docker-compose")
            or path.name in {"compose.yaml", "compose.yml", "schema.prisma"}
            or path.suffix in {".gradle", ".kts"}
        ]
        seen: set[str] = set()
        for path in candidates:
            relative = context.relative(path)
            text = context.text(path).lower()
            for marker, technology in self.MARKERS.items():
                if marker in text and technology not in seen:
                    result.facts.append(_fact("data", technology, "present", relative))
                    seen.add(technology)
        return result


class MessagingAnalyzer:
    MARKERS = {
        "rabbitmq": "RabbitMQ",
        "aio-pika": "RabbitMQ",
        "bullmq": "BullMQ",
        "celery": "Celery",
        "kafka": "Kafka",
        "nats": "NATS",
    }

    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        seen: set[str] = set()
        for path in context.files:
            if path.suffix not in {".json", ".toml", ".txt", ".yaml", ".yml"}:
                continue
            relative = context.relative(path)
            text = context.text(path).lower()
            for marker, technology in self.MARKERS.items():
                if marker in text and technology not in seen:
                    result.facts.append(_fact("messaging", technology, "present", relative))
                    seen.add(technology)
        return result


class InfrastructureAnalyzer:
    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        seen: set[str] = set()
        for path in context.files:
            relative = context.relative(path)
            technology: str | None = None
            if path.name.startswith("Dockerfile"):
                technology = "Docker"
            elif path.name in {
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }:
                technology = "Docker Compose"
            elif relative.startswith(".github/workflows/") and path.suffix in {".yml", ".yaml"}:
                technology = "GitHub Actions"
            elif path.suffix == ".tf":
                technology = "Terraform"
            elif "nginx" in relative.lower() and path.suffix in {".conf", ""}:
                technology = "Nginx"
            elif path.suffix in {".yaml", ".yml"}:
                text = context.text(path)
                if "apiVersion:" in text and "kind:" in text:
                    technology = "Kubernetes"
            if technology and technology not in seen:
                result.facts.append(
                    _fact(
                        "infrastructure",
                        technology,
                        "present",
                        relative,
                        kind=EvidenceKind.CONFIG,
                    )
                )
                seen.add(technology)
        return result


class TestAnalyzer:
    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        tests = [path for path in context.files if _is_test_file(path)]
        if tests:
            result.facts.append(
                KnowledgeFact(
                    category="testing",
                    name="test_files",
                    value=str(len(tests)),
                    confidence=Confidence.CONFIRMED,
                    evidence=Evidence(
                        context.relative(tests[0]), EvidenceKind.FILE, "test filename convention"
                    ),
                )
            )
        return result


def _is_test_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".spec." in name
        or ".test." in name
        or "/test/" in path.as_posix().lower()
        or "/tests/" in path.as_posix().lower()
    )


class ApiAnalyzer:
    FASTAPI_RE = re.compile(
        r"@(?P<router>[A-Za-z_][\w.]*)\."
        r"(?P<method>get|post|put|patch|delete)\(\s*[\"']"
        r"(?P<path>[^\"']+)"
    )
    NEST_CONTROLLER_RE = re.compile(r"@Controller\(\s*[\"']?(?P<path>[^\"')]+)")
    NEST_METHOD_RE = re.compile(
        r"@(?P<method>Get|Post|Put|Patch|Delete)\(\s*[\"']?(?P<path>[^\"')]+)?"
    )

    def analyze(self, context: ScanContext) -> AnalysisResult:
        result = AnalysisResult()
        for path in context.files:
            if path.suffix not in {".py", ".ts"}:
                continue
            relative = context.relative(path)
            text = context.text(path)
            for match in self.FASTAPI_RE.finditer(text):
                result.facts.append(
                    _fact(
                        "api",
                        match.group("method").upper(),
                        match.group("path"),
                        relative,
                        kind=EvidenceKind.SOURCE,
                    )
                )
            controller = self.NEST_CONTROLLER_RE.search(text) if path.suffix == ".ts" else None
            if controller:
                base = controller.group("path").strip() or "/"
                for match in self.NEST_METHOD_RE.finditer(text):
                    suffix = (match.group("path") or "").strip()
                    endpoint = "/".join(part.strip("/") for part in (base, suffix) if part)
                    result.facts.append(
                        _fact(
                            "api",
                            match.group("method").upper(),
                            f"/{endpoint}" if endpoint else "/",
                            relative,
                            kind=EvidenceKind.SOURCE,
                        )
                    )
        return result


def unique_facts(facts: Iterable[KnowledgeFact]) -> list[KnowledgeFact]:
    unique: dict[tuple[str, str, str], KnowledgeFact] = {}
    for fact in facts:
        unique[(fact.category, fact.name, fact.value)] = fact
    return sorted(unique.values(), key=lambda item: (item.category, item.name, item.value))
