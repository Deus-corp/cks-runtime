"""
Runtime Diagnostic.

Represents an immutable operational observation.

Diagnostics never modify Runtime state and never redefine
CKS Core semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID
from uuid import uuid4


class DiagnosticSource(str, Enum):
    """
    Origin of a Runtime Diagnostic.
    """

    CORE = "core"
    RUNTIME = "runtime"


class DiagnosticSeverity(str, Enum):
    """
    Runtime Diagnostic severity.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """
    Immutable Runtime Diagnostic.

    Ownership:

    - Core Diagnostics originate from CKS Core;
    - Runtime Diagnostics originate from Runtime;
    - Runtime preserves Diagnostics without modifying them.
    """

    message: str

    source: DiagnosticSource

    severity: DiagnosticSeverity

    code: str | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )

    diagnostic_id: UUID = field(
        default_factory=uuid4,
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(
            UTC,
        ),
    )

    #
    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    #

    @property
    def has_code(self) -> bool:
        """
        Whether this Diagnostic has a diagnostic code.
        """

        return self.code is not None

    @property
    def has_metadata(self) -> bool:
        """
        Whether this Diagnostic contains metadata.
        """

        return bool(self.metadata)

    @property
    def is_error(self) -> bool:
        return self.severity is DiagnosticSeverity.ERROR

    @property
    def is_warning(self) -> bool:
        return self.severity is DiagnosticSeverity.WARNING

    @property
    def is_info(self) -> bool:
        return self.severity is DiagnosticSeverity.INFO

    #
    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    #

    def __str__(self) -> str:
        """
        Human-readable representation.
        """

        prefix = (
            f"[{self.code}] "
            if self.code is not None
            else ""
        )

        return (
            f"{prefix}"
            f"[{self.severity.value.upper()}] "
            f"{self.source.value}: "
            f"{self.message}"
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.diagnostic_id}, "
            f"severity={self.severity.value!r}, "
            f"source={self.source.value!r})"
        )