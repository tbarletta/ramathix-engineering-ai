from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from ..config import Settings
from .skills import (
    CommandSkillGenerator,
    DockerSkillRunner,
    SkillGapDetector,
    SkillLifecycle,
    SkillRegistry,
    SkillRuntime,
    SkillValidator,
)

app = typer.Typer(help="Ciclo autônomo e governado de skills do REA.")


def _root() -> Path:
    return Settings.from_env().home / ".rea" / "skills"


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text("utf-8").splitlines()
        if line.strip()
    ]


def _lifecycle(image: str) -> SkillLifecycle:
    return SkillLifecycle(_root(), SkillValidator(DockerSkillRunner(image)))


@app.command("discover")
def discover(
    audit: Path = typer.Option(Path(".rea/audit.jsonl"), "--audit"),
    minimum_occurrences: int = typer.Option(2, min=1),
) -> None:
    proposals = SkillGapDetector(minimum_occurrences).detect(_events(audit))
    typer.echo(json.dumps([asdict(item) for item in proposals], ensure_ascii=False, indent=2))


@app.command("install")
def install(
    package: Path,
    image: str = typer.Option("python:3.12-slim", "--image"),
    approve_permission: list[str] = typer.Option([], "--approve-permission"),
    no_activate: bool = typer.Option(False, "--no-activate"),
) -> None:
    record = _lifecycle(image).install(
        package.resolve(),
        approvals=set(approve_permission),
        activate=not no_activate,
    )
    typer.echo(json.dumps(asdict(record), ensure_ascii=False, indent=2))


@app.command("cycle")
def cycle(
    generator_command: list[str] = typer.Option(..., "--generator-command"),
    audit: Path = typer.Option(Path(".rea/audit.jsonl"), "--audit"),
    image: str = typer.Option("python:3.12-slim", "--image"),
    approve_permission: list[str] = typer.Option([], "--approve-permission"),
    minimum_occurrences: int = typer.Option(2, min=1),
    max_skills: int = typer.Option(1, min=1, max=10),
) -> None:
    lifecycle = _lifecycle(image)
    created = lifecycle.run_once(
        _events(audit),
        CommandSkillGenerator(tuple(generator_command)),
        approvals=set(approve_permission),
        minimum_occurrences=minimum_occurrences,
        max_skills=max_skills,
    )
    typer.echo(json.dumps([asdict(item) for item in created], ensure_ascii=False, indent=2))


@app.command("record")
def record(
    skill_id: str,
    success: bool = typer.Option(..., "--success/--failure"),
    latency_seconds: float = typer.Option(..., "--latency"),
    cost: float = typer.Option(0.0, "--cost"),
    safety_failure: bool = typer.Option(False, "--safety-failure"),
) -> None:
    record = _lifecycle("python:3.12-slim").record(
        skill_id,
        success=success,
        latency_seconds=latency_seconds,
        cost=cost,
        safety_failure=safety_failure,
    )
    typer.echo(json.dumps(asdict(record), ensure_ascii=False, indent=2))


@app.command("invoke")
def invoke(
    skill_id: str,
    payload: str = typer.Option("{}", "--payload"),
    image: str = typer.Option("python:3.12-slim", "--image"),
    grant_permission: list[str] = typer.Option([], "--grant-permission"),
) -> None:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise typer.BadParameter("--payload deve ser um objeto JSON")
    lifecycle = _lifecycle(image)
    result = SkillRuntime(lifecycle, image=image).invoke(
        skill_id,
        data,
        granted_permissions=set(grant_permission),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("reconcile")
def reconcile(
    image: str = typer.Option("python:3.12-slim", "--image"),
) -> None:
    changed = _lifecycle(image).reconcile()
    typer.echo(json.dumps([asdict(item) for item in changed], ensure_ascii=False, indent=2))


@app.command("rollback")
def rollback(skill_id: str, reason: str = typer.Option(..., "--reason")) -> None:
    record = _lifecycle("python:3.12-slim").rollback(skill_id, reason)
    typer.echo(json.dumps(asdict(record), ensure_ascii=False, indent=2))


@app.command("quarantine")
def quarantine(skill_id: str, reason: str = typer.Option(..., "--reason")) -> None:
    record = _lifecycle("python:3.12-slim").quarantine(skill_id, reason)
    typer.echo(json.dumps(asdict(record), ensure_ascii=False, indent=2))


@app.command("status")
def status() -> None:
    records = SkillRegistry(_root()).load()
    payload = {
        skill_id: [asdict(item) for item in versions]
        for skill_id, versions in records.items()
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
