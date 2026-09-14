from __future__ import annotations

import json
from pathlib import Path

import typer

from .discovery import AuditObserver, OpportunityDetector
from .store import JsonEvolutionStore

app = typer.Typer(help="REA autonomous evolution control plane.")


@app.command("discover")
def discover(
    repository: str = typer.Option(..., "--repo"),
    audit_path: Path = typer.Option(Path(".rea/audit.jsonl"), "--audit"),
    minimum_occurrences: int = typer.Option(2, "--minimum-occurrences", min=1),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Discover evidence-backed opportunities without changing a repository."""
    records = []
    if audit_path.exists():
        records = [
            json.loads(line)
            for line in audit_path.read_text("utf-8").splitlines()
            if line.strip()
        ]
    events = AuditObserver().observe(records, repository)
    hypotheses = OpportunityDetector(minimum_occurrences).detect(events)
    payload = [item.__dict__ for item in hypotheses]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    typer.echo(rendered)


@app.command("lessons")
def lessons(
    repository: str = typer.Option(..., "--repo"),
    root: Path = typer.Option(Path(".rea/evolution"), "--root"),
) -> None:
    """Show durable lessons learned from accepted and rejected experiments."""
    payload = [item.__dict__ for item in JsonEvolutionStore(root).lessons(repository)]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
