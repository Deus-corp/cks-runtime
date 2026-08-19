"""
Runtime Metrics Collector.

Tracks invocation counts and total execution time for each operation
type, keyed by operation_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MetricsCollector:
    """Collects runtime metrics for operations."""

    _counts: dict[str, int] = field(default_factory=dict)
    _total_time_ms: dict[str, float] = field(default_factory=dict)

    def record(self, operation_id: str, duration_ms: float) -> None:
        self._counts[operation_id] = self._counts.get(operation_id, 0) + 1
        self._total_time_ms[operation_id] = (
            self._total_time_ms.get(operation_id, 0.0) + duration_ms
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable metrics snapshot."""
        return {
            op_id: {
                "count": self._counts.get(op_id, 0),
                "total_time_ms": round(self._total_time_ms.get(op_id, 0.0), 2),
                "avg_time_ms": round(
                    self._total_time_ms.get(op_id, 0.0) / self._counts.get(op_id, 1), 2
                ),
            }
            for op_id in self._counts
        }