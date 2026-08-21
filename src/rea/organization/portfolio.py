from __future__ import annotations

from collections import defaultdict, deque

from .contracts import WorkUnit


class PortfolioValidationError(RuntimeError):
    pass


class DependencyCycleError(PortfolioValidationError):
    pass


class PortfolioPlanner:
    """Validates and orders AI-proposed work deterministically."""

    def prioritize(self, work_units: list[WorkUnit]) -> list[WorkUnit]:
        self._validate(work_units)
        by_id = {item.id: item for item in work_units}
        ordered_ids = self._topological_order(work_units)

        for item in work_units:
            item.priority_score = self._score(item)

        dependency_depth: dict[str, int] = {}
        for unit_id in ordered_ids:
            unit = by_id[unit_id]
            dependency_depth[unit_id] = 0 if not unit.dependencies else 1 + max(
                dependency_depth[dep] for dep in unit.dependencies
            )

        ordered = sorted(
            work_units,
            key=lambda item: (
                dependency_depth[item.id],
                -item.priority_score,
                item.id,
            ),
        )
        for rank, item in enumerate(ordered, start=1):
            item.priority_rank = rank
        return ordered

    @staticmethod
    def _score(item: WorkUnit) -> int:
        # Inputs come from the manager, but the weighting is owned by code/governance.
        return (
            item.business_value * 5
            + item.strategic_fit * 4
            + item.urgency * 3
            - item.effort * 2
        )

    @staticmethod
    def _validate(work_units: list[WorkUnit]) -> None:
        ids = [item.id for item in work_units]
        if len(ids) != len(set(ids)):
            raise PortfolioValidationError("duplicate work unit IDs")
        known = set(ids)
        for item in work_units:
            if not 1 <= item.business_value <= 5:
                raise PortfolioValidationError(f"invalid business_value for {item.id}")
            if not 1 <= item.urgency <= 5:
                raise PortfolioValidationError(f"invalid urgency for {item.id}")
            if not 1 <= item.strategic_fit <= 5:
                raise PortfolioValidationError(f"invalid strategic_fit for {item.id}")
            if not 1 <= item.effort <= 5:
                raise PortfolioValidationError(f"invalid effort for {item.id}")
            unknown = set(item.dependencies) - known
            if unknown:
                raise PortfolioValidationError(
                    f"{item.id} has unknown dependencies: {sorted(unknown)}"
                )
            if item.id in item.dependencies:
                raise PortfolioValidationError(f"{item.id} depends on itself")

    @staticmethod
    def _topological_order(work_units: list[WorkUnit]) -> list[str]:
        indegree = {item.id: len(item.dependencies) for item in work_units}
        children: dict[str, list[str]] = defaultdict(list)
        for item in work_units:
            for dependency in item.dependencies:
                children[dependency].append(item.id)

        queue = deque(sorted(item for item, degree in indegree.items() if degree == 0))
        ordered: list[str] = []
        while queue:
            current = queue.popleft()
            ordered.append(current)
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if len(ordered) != len(work_units):
            raise DependencyCycleError("work unit dependency cycle detected")
        return ordered
