from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .contracts import (
    EvaluationResult,
    EvolutionExperiment,
    EvolutionHypothesis,
    EvolutionMetrics,
    ExperimentStatus,
    Lesson,
)


class JsonEvolutionStore:
    """Crash-safe experiment and lesson persistence under .rea/evolution."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.experiments = root / "experiments"
        self.lessons_path = root / "lessons.jsonl"

    def save_experiment(self, experiment: EvolutionExperiment) -> Path:
        path = self.experiments / f"{experiment.id}.json"
        self._atomic_json(path, experiment.to_dict())
        return path

    def load_experiment(self, experiment_id: str) -> EvolutionExperiment:
        data = json.loads((self.experiments / f"{experiment_id}.json").read_text("utf-8"))
        hypothesis = EvolutionHypothesis(**data["hypothesis"])
        evaluation = data.get("evaluation")
        result = None
        if evaluation:
            result = EvaluationResult(
                baseline=EvolutionMetrics(**evaluation["baseline"]),
                candidate=EvolutionMetrics(**evaluation["candidate"]),
                baseline_score=evaluation["baseline_score"],
                candidate_score=evaluation["candidate_score"],
                delta=evaluation["delta"],
                critical_regressions=tuple(evaluation["critical_regressions"]),
                passed=evaluation["passed"],
            )
        return EvolutionExperiment(
            id=data["id"],
            hypothesis=hypothesis,
            baseline_ref=data["baseline_ref"],
            candidate_ref=data.get("candidate_ref"),
            status=ExperimentStatus(data["status"]),
            evaluation=result,
            pull_request_url=data.get("pull_request_url"),
            reason=data.get("reason", ""),
        )

    def append_lesson(self, lesson: Lesson) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lessons_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(lesson), ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def lessons(self, repository: str, fingerprint: str | None = None) -> list[Lesson]:
        if not self.lessons_path.exists():
            return []
        result = []
        for line in self.lessons_path.read_text("utf-8").splitlines():
            item: dict[str, Any] = json.loads(line)
            if item["repository"] != repository:
                continue
            if fingerprint is not None and item["fingerprint"] != fingerprint:
                continue
            item["evidence"] = tuple(item["evidence"])
            result.append(Lesson(**item))
        return result

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
