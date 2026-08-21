import json
from pathlib import Path

from rea.audit import AuditLog
from rea.domain import Decision
from rea.knowledge.scanner import RepositoryScanner
from rea.policy import CommandPolicy


def scanner(tmp_path: Path) -> RepositoryScanner:
    policy = CommandPolicy(default=Decision.ASK, rules=[])
    return RepositoryScanner(policy=policy, audit=AuditLog(tmp_path / "audit.jsonl"))


def test_scanner_discovers_ramathix_style_stack(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@nestjs/core": "^10",
                    "@prisma/client": "^5",
                    "bullmq": "^5",
                    "next": "^14",
                    "react": "^18",
                },
                "devDependencies": {"typescript": "^5", "jest": "^29"},
            }
        )
    )
    (tmp_path / "schema.prisma").write_text(
        'datasource db { provider = "postgresql" url = env("DATABASE_URL") }'
    )
    (tmp_path / "compose.yaml").write_text(
        "services:\n  redis:\n    image: redis:7\n  rabbitmq:\n    image: rabbitmq:4\n"
    )
    (tmp_path / "Dockerfile").write_text("FROM node:22-alpine\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "posts.controller.ts").write_text(
        "import { Controller, Get } from '@nestjs/common';\n"
        "@Controller('posts')\nexport class PostsController {\n@Get(':id')\nfind() {}\n}\n"
    )
    (src / "posts.service.spec.ts").write_text("export const test = true;\n")

    inventory = scanner(tmp_path).scan(tmp_path, include_git=False)

    facts = {(fact.category, fact.name, fact.value) for fact in inventory.facts}
    assert ("framework", "NestJS", "^10") in facts
    assert ("framework", "Next.js", "^14") in facts
    assert ("framework", "Prisma", "^5") in facts
    assert ("messaging", "RabbitMQ", "present") in facts
    assert ("messaging", "BullMQ", "present") in facts
    assert ("data", "PostgreSQL", "present") in facts
    assert ("infrastructure", "Docker", "present") in facts
    assert ("infrastructure", "Docker Compose", "present") in facts
    assert ("testing", "test_files", "1") in facts
    assert ("api", "GET", "/posts/:id") in facts
    assert inventory.languages["TypeScript"] == 2
    assert "package.json" in inventory.manifests
    assert any(edge.target == "@nestjs/common" for edge in inventory.dependencies)


def test_scanner_discovers_python_symbols_and_imports(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="sample"\ndependencies=["fastapi>=0.115", "sqlalchemy>=2", "pytest>=8"]\n'
    )
    (tmp_path / "main.py").write_text(
        "import json\n"
        "from fastapi import FastAPI\n"
        "class Service:\n"
        "    pass\n\n"
        "def build():\n"
        "    return Service()\n"
    )
    inventory = scanner(tmp_path).scan(tmp_path, include_git=False)
    assert {symbol.name for symbol in inventory.symbols} >= {"Service", "build"}
    assert any(edge.target == "fastapi" for edge in inventory.dependencies)
    facts = {(fact.category, fact.name) for fact in inventory.facts}
    assert ("framework", "FastAPI") in facts
    assert ("framework", "SQLAlchemy") in facts


def test_scanner_ignores_symlinks_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("SECRET = 'do-not-read'\n")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        return

    inventory = scanner(tmp_path).scan(tmp_path, include_git=False)
    assert inventory.file_count == 0
    assert not inventory.symbols
