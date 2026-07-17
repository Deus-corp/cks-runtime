"""
Runtime Diagnostic Aggregator.

Collects Runtime and Core Diagnostics.

Aggregation preserves ordering, ownership and immutability.

Diagnostics are never modified after insertion.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
from typing import Any

from .diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)


class DiagnosticAggregator:
    """
    Collects Runtime Diagnostics.

    The aggregator owns only the collection.

    Diagnostics remain immutable.
    """

    def __init__(self) -> None:
        self._diagnostics: list[Any] = []

    #
    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    #

    def add(self, diagnostic: Any) -> None:
        """Add one diagnostic (Core or Runtime)."""
        self._diagnostics.append(diagnostic)

    def extend(self, diagnostics: Iterable[Any]) -> None:
        """Add multiple diagnostics."""
        self._diagnostics.extend(diagnostics)

    def clear(self) -> None:
        """Remove every collected Diagnostic."""
        self._diagnostics.clear()

    #
    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    #

    def all(self) -> tuple[Any, ...]:
        """Return every collected Diagnostic."""
        return tuple(self._diagnostics)

    def by_source(self, source: DiagnosticSource) -> tuple[Diagnostic, ...]:
        """Return Diagnostics originating from the given source (only Diagnostic instances)."""
        return tuple(
            d for d in self._diagnostics
            if isinstance(d, Diagnostic) and d.source is source
        )

    def by_severity(self, severity: DiagnosticSeverity) -> tuple[Diagnostic, ...]:
        """Return Diagnostics having the given severity (only Diagnostic instances)."""
        return tuple(
            d for d in self._diagnostics
            if isinstance(d, Diagnostic) and d.severity is severity
        )

    #
    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    #

    @property
    def empty(self) -> bool:
        """Whether no Diagnostics exist."""
        return not self._diagnostics

    @property
    def has_errors(self) -> bool:
        return any(
            isinstance(d, Diagnostic) and d.is_error
            for d in self._diagnostics
        )

    @property
    def has_warnings(self) -> bool:
        return any(
            isinstance(d, Diagnostic) and d.is_warning
            for d in self._diagnostics
        )

    @property
    def has_infos(self) -> bool:
        return any(
            isinstance(d, Diagnostic) and d.is_info
            for d in self._diagnostics
        )

    @property
    def error_count(self) -> int:
        return sum(
            1 for d in self._diagnostics
            if isinstance(d, Diagnostic) and d.is_error
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1 for d in self._diagnostics
            if isinstance(d, Diagnostic) and d.is_warning
        )

    @property
    def info_count(self) -> int:
        return sum(
            1 for d in self._diagnostics
            if isinstance(d, Diagnostic) and d.is_info
        )

    def count(self) -> int:
        """Total number of Diagnostics."""
        return len(self._diagnostics)

    #
    # ------------------------------------------------------------------
    # Collection API
    # ------------------------------------------------------------------
    #

    def __len__(self) -> int:
        return len(self._diagnostics)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._diagnostics)

    def __contains__(self, diagnostic: object) -> bool:
        return diagnostic in self._diagnostics