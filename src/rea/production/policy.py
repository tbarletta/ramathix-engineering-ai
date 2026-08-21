from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProductionCapability(StrEnum):
    LOGS_READ = "logs_read"
    METRICS_READ = "metrics_read"
    TRACES_READ = "traces_read"
    DEPLOYMENTS_READ = "deployments_read"
    SOURCE_READ = "source_read"
    DATABASE_READ = "database_read"
    QUEUE_READ = "queue_read"
    INFRASTRUCTURE_READ = "infrastructure_read"


class ProductionAccessDenied(RuntimeError):
    pass


class ProductionWriteDenied(ProductionAccessDenied):
    pass


@dataclass(frozen=True)
class ProductionReadPolicy:
    level: int = 2
    allowed: frozenset[ProductionCapability] = frozenset(ProductionCapability)

    def authorize(self, capability: ProductionCapability, *, write: bool = False) -> None:
        if write:
            raise ProductionWriteDenied(
                f"production writes are disabled in V0.6: {capability.value}"
            )
        if capability not in self.allowed:
            raise ProductionAccessDenied(
                f"production read capability is not allowed: {capability.value}"
            )

    def describe(self) -> dict:
        return {
            "level": self.level,
            "mode": "read_only",
            "allowed": sorted(item.value for item in self.allowed),
            "writes": "deny",
        }
