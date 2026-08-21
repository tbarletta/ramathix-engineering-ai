from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..execution import GovernedLocalRunner
from .contracts import IncidentRequest, ProductionSignal, SignalKind
from .policy import ProductionCapability, ProductionReadPolicy


class ProductionSignalProvider(Protocol):
    def collect(self, request: IncidentRequest) -> list[ProductionSignal]: ...


_KIND_CAPABILITY = {
    SignalKind.LOG: ProductionCapability.LOGS_READ,
    SignalKind.METRIC: ProductionCapability.METRICS_READ,
    SignalKind.TRACE: ProductionCapability.TRACES_READ,
    SignalKind.DEPLOYMENT: ProductionCapability.DEPLOYMENTS_READ,
    SignalKind.GIT_CHANGE: ProductionCapability.SOURCE_READ,
    SignalKind.DATABASE: ProductionCapability.DATABASE_READ,
    SignalKind.QUEUE: ProductionCapability.QUEUE_READ,
    SignalKind.INFRASTRUCTURE: ProductionCapability.INFRASTRUCTURE_READ,
}


class JsonFileSignalProvider:
    """Reads exported JSON/JSONL observability data. It has no write interface."""

    def __init__(
        self,
        path: Path,
        *,
        policy: ProductionReadPolicy,
        max_signals: int = 500,
    ) -> None:
        self.path = path
        self.policy = policy
        self.max_signals = max_signals

    def collect(self, request: IncidentRequest) -> list[ProductionSignal]:
        rows = self._rows()
        signals: list[ProductionSignal] = []
        for index, row in enumerate(rows):
            service = str(row.get("service") or "")
            if service and service != request.service:
                continue
            kind = SignalKind(str(row.get("kind") or "log"))
            self.policy.authorize(_KIND_CAPABILITY[kind])
            signals.append(
                ProductionSignal(
                    evidence_id=str(
                        row.get("evidence_id")
                        or f"{self.path.stem}-{index + 1:04d}"
                    ),
                    timestamp=str(row.get("timestamp") or ""),
                    kind=kind,
                    source=str(row.get("source") or self.path.name),
                    service=service or request.service,
                    summary=str(row.get("summary") or ""),
                    severity=str(row.get("severity") or "info"),
                    attributes=dict(row.get("attributes") or {}),
                )
            )
            if len(signals) >= self.max_signals:
                break
        return signals

    def _rows(self) -> list[dict]:
        text = self.path.read_text(encoding="utf-8")
        if self.path.suffix.lower() == ".jsonl":
            return [
                json.loads(line)
                for line in text.splitlines()
                if line.strip()
            ]
        payload = json.loads(text)
        if isinstance(payload, list):
            return [dict(item) for item in payload]
        if isinstance(payload, dict) and isinstance(payload.get("signals"), list):
            return [dict(item) for item in payload["signals"]]
        raise ValueError(f"unsupported signal snapshot format: {self.path}")


class GitHistorySignalProvider:
    """Reads recent Git commits and changed paths through the governed read-only runner."""

    def __init__(
        self,
        repository: Path,
        *,
        policy: ProductionReadPolicy,
        runner: GovernedLocalRunner,
        max_commits: int = 20,
    ) -> None:
        self.repository = repository.resolve()
        self.policy = policy
        self.runner = runner
        self.max_commits = max(1, min(max_commits, 100))

    def collect(self, request: IncidentRequest) -> list[ProductionSignal]:
        self.policy.authorize(ProductionCapability.SOURCE_READ)
        result = self.runner.run(
            [
                "git",
                "log",
                "-n",
                str(self.max_commits),
                "--date=iso-strict",
                "--pretty=format:%H%x1f%cI%x1f%s",
            ],
            cwd=self.repository,
            actor="production_git_read",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git log failed")

        signals: list[ProductionSignal] = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) != 3:
                continue
            sha, timestamp, subject = parts
            files = self._changed_files(sha)
            signals.append(
                ProductionSignal(
                    evidence_id=f"git-{sha[:12]}",
                    timestamp=timestamp,
                    kind=SignalKind.GIT_CHANGE,
                    source="git",
                    service=request.service,
                    summary=subject,
                    attributes={"sha": sha, "files": files},
                )
            )
        return signals

    def _changed_files(self, sha: str) -> list[str]:
        result = self.runner.run(
            ["git", "show", "--format=", "--name-only", "--no-renames", sha],
            cwd=self.repository,
            actor="production_git_read",
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()][:200]


class CompositeSignalProvider:
    def __init__(self, providers: list[ProductionSignalProvider]) -> None:
        self.providers = providers

    def collect(self, request: IncidentRequest) -> list[ProductionSignal]:
        signals: list[ProductionSignal] = []
        seen: set[str] = set()
        for provider in self.providers:
            for signal in provider.collect(request):
                if signal.evidence_id in seen:
                    continue
                seen.add(signal.evidence_id)
                signals.append(signal)
        return sorted(signals, key=lambda item: (item.timestamp, item.evidence_id))
