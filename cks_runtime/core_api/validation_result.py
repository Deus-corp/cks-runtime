"""
Runtime Validation Result.

Represents the Runtime view of semantic validation.

This object is intentionally independent from the
internal ValidationResult implementation of CKS Core.

The Core Adapter is responsible for translating
Core-specific validation objects into this Runtime model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeValidationResult:
    """
    Runtime representation of semantic validation.

    Runtime consumes only this abstraction.

    CKS Core may evolve independently.
    """

    valid: bool

    diagnostics: tuple[Any, ...] = field(
        default_factory=tuple,
    )

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )

    @property
    def has_diagnostics(self) -> bool:
        """
        Whether validation produced diagnostics.
        """

        return bool(self.diagnostics)

    @property
    def diagnostic_count(self) -> int:
        """
        Number of diagnostics.
        """

        return len(self.diagnostics)