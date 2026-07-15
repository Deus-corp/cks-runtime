"""
Runtime Diagnostic model.

Runtime Diagnostics describe operational observations.

They never modify Runtime state and never redefine
CKS Core semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class DiagnosticSource(str, Enum):
    """
    Origin of a Diagnostic.
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

    - Core Diagnostics belong to CKS Core;
    - Runtime Diagnostics belong to Runtime;
    - Runtime preserves diagnostics without modifying them.
    """

    message: str

    source: DiagnosticSource

    severity: DiagnosticSeverity

    code: str | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    diagnostic_id: UUID = field(
        default_factory=uuid4
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )