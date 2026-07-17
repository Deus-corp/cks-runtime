"""
Runtime Validation Result.

Represents the Runtime-native view of semantic validation.

Runtime never depends on Core-native validation objects.

Core plugins translate their validation models into
this stable Runtime representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType
from typing import Any
from typing import Mapping


@dataclass(
    frozen=True,
    slots=True,
)
class RuntimeValidationResult:
    """
    Immutable Runtime validation result.

    Ownership:

    Runtime owns this representation.

    Core plugins own:
        - validation rules;
        - semantic diagnostics;
        - validation algorithms.

    Runtime only consumes the result.
    """

    valid: bool

    diagnostics: tuple[Any, ...] = field(
        default_factory=tuple,
    )

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
    )

    def __post_init__(
        self,
    ) -> None:
        """
        Normalize mutable inputs into immutable structures.
        """

        object.__setattr__(
            self,
            "diagnostics",
            tuple(
                self.diagnostics,
            ),
        )

        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(
                dict(
                    self.metadata,
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def has_diagnostics(
        self,
    ) -> bool:
        """
        Whether validation produced diagnostics.
        """

        return bool(
            self.diagnostics,
        )

    @property
    def diagnostic_count(
        self,
    ) -> int:
        """
        Number of diagnostics.
        """

        return len(
            self.diagnostics,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __bool__(
        self,
    ) -> bool:
        """
        Validation truth value.

        True means validation succeeded.
        """

        return self.valid

    @classmethod
    def success(
        cls,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeValidationResult:
        """
        Construct successful validation result.
        """

        return cls(
            valid=True,
            metadata=(
                metadata
                if metadata is not None
                else {}
            ),
        )

    @classmethod
    def failure(
        cls,
        diagnostics: tuple[Any, ...],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeValidationResult:
        """
        Construct failed validation result.
        """

        return cls(
            valid=False,
            diagnostics=diagnostics,
            metadata=(
                metadata
                if metadata is not None
                else {}
            ),
        )