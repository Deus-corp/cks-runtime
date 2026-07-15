"""
Runtime Diagnostic Aggregator.

Aggregates Runtime and Core diagnostics.

Aggregation preserves ordering and ownership.
"""

from __future__ import annotations

from typing import Iterable

from .diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)


class DiagnosticAggregator:
    """
    Collects Runtime Diagnostics.

    Diagnostics remain immutable.

    Aggregation never modifies their meaning.
    """

    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []


    def add(
        self,
        diagnostic: Diagnostic,
    ) -> None:
        """
        Add a Diagnostic.
        """

        self._diagnostics.append(
            diagnostic
        )


    def extend(
        self,
        diagnostics: Iterable[Diagnostic],
    ) -> None:
        """
        Add multiple Diagnostics.
        """

        self._diagnostics.extend(
            diagnostics
        )


    def clear(
        self,
    ) -> None:
        """
        Remove all Diagnostics.
        """

        self._diagnostics.clear()


    def all(
        self,
    ) -> tuple[Diagnostic, ...]:
        """
        Return an immutable snapshot.
        """

        return tuple(
            self._diagnostics
        )


    def count(
        self,
    ) -> int:
        return len(
            self._diagnostics
        )


    def has_errors(
        self,
    ) -> bool:
        """
        True if any Runtime Diagnostic has ERROR severity.
        """

        return any(
            diagnostic.severity
            is DiagnosticSeverity.ERROR
            for diagnostic in self._diagnostics
        )


    def __len__(
        self,
    ) -> int:
        return len(
            self._diagnostics
        )


    def __iter__(
        self,
    ):
        return iter(
            self._diagnostics
        )