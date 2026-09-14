from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .discovery import AuditObserver, OpportunityDetector
from .state import EvolutionState, RepositoryLock, StateStore
from .workflow import EvolutionBudget, EvolutionWorkflow


class EvolutionWorker:
    def __init__(
        self,
        *,
        repository: str,
        audit_path: Path,
        workflow: EvolutionWorkflow,
        state_store: StateStore,
        lock_path: Path,
        minimum_occurrences: int = 2,
        event_source: Callable[[], list[dict]] | None = None,
        promotion_handler: Callable[[dict], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.audit_path = audit_path
        self.workflow = workflow
        self.state_store = state_store
        self.lock_path = lock_path
        self.detector = OpportunityDetector(minimum_occurrences)
        self.event_source = event_source or (lambda: [])
        self.promotion_handler = promotion_handler or (lambda _item: False)

    def tick(
        self,
        *,
        baseline_ref: str,
        benchmark: str,
        budget: EvolutionBudget,
    ) -> int:
        with RepositoryLock(self.lock_path):
            state = self.state_store.load()
            self._roll_budget_day(state)
            self._reconcile_promotions(state)
            if state.active_experiment:
                state.consecutive_failures += 1
                state.active_experiment = None
            self.workflow.consecutive_failures = state.consecutive_failures
            hypotheses = self.detector.detect(
                AuditObserver().observe(
                    [*self._records(), *self.event_source()], self.repository
                )
            )
            completed = 0
            known = set(state.processed_fingerprints)
            remaining_gpu = budget.max_gpu_minutes - state.gpu_minutes_today
            for hypothesis in hypotheses:
                if hypothesis.id in known or completed >= budget.max_experiments:
                    continue
                if state.experiments_today >= budget.max_experiments or remaining_gpu <= 0:
                    break
                hypothesis.max_gpu_minutes = min(hypothesis.max_gpu_minutes, int(remaining_gpu))
                state.active_experiment = hypothesis.id
                self.state_store.save(state)
                try:
                    experiment, _ = self.workflow.run(
                        hypothesis,
                        baseline_ref=baseline_ref,
                        benchmark=benchmark,
                        budget=budget,
                    )
                    used = (
                        experiment.evaluation.candidate.gpu_minutes
                        if experiment.evaluation
                        else 0
                    )
                    state.gpu_minutes_today += used
                    remaining_gpu -= used
                    state.consecutive_failures = self.workflow.consecutive_failures
                    state.processed_fingerprints.append(hypothesis.id)
                    state.experiments_today += 1
                    if _.allowed and experiment.pull_request_url:
                        state.promotion_queue.append(
                            {
                                "experiment_id": experiment.id,
                                "pull_request_url": experiment.pull_request_url,
                            }
                        )
                    completed += 1
                finally:
                    state.active_experiment = None
                    self.state_store.save(state)
            self._reconcile_promotions(state)
            self.state_store.save(state)
            return completed

    def daemon(
        self,
        *,
        baseline_ref: str,
        benchmark: str,
        budget: EvolutionBudget,
        interval_seconds: int = 3600,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        if interval_seconds < 60:
            raise ValueError("interval_seconds must be at least 60")
        should_stop = stop or (lambda: False)
        while not should_stop():
            self.tick(baseline_ref=baseline_ref, benchmark=benchmark, budget=budget)
            time.sleep(interval_seconds)

    def _reconcile_promotions(self, state: EvolutionState) -> None:
        pending = []
        for item in state.promotion_queue:
            try:
                if not self.promotion_handler(item):
                    pending.append(item)
            except Exception:
                pending.append(item)
        state.promotion_queue = pending

    def _records(self) -> list[dict]:
        if not self.audit_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.audit_path.read_text("utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _roll_budget_day(state: EvolutionState) -> None:
        today = datetime.now(UTC).date().isoformat()
        if state.budget_date != today:
            state.budget_date = today
            state.experiments_today = 0
            state.gpu_minutes_today = 0
