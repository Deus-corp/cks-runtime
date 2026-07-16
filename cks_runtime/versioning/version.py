"""
Immutable Runtime Version.

A Runtime Version records the operational state of a
Runtime Session immediately following a committed Transaction.

Versions are immutable snapshots.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RuntimeVersion:
    """
    Immutable Runtime Version.

    Ownership:

    - owned by exactly one Runtime Session;
    - created from exactly one committed Transaction;
    - records operational state only.
    """

    session_id: str

    transaction_id: str

    knowledge_structure: Any

    metadata: dict[str, Any]

    version_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )

    def __post_init__(
        self,
    ) -> None:
        """
        Store immutable snapshots.

        Runtime Versions must never share mutable
        objects with a live Runtime Session.
        """

        object.__setattr__(
            self,
            "knowledge_structure",
            deepcopy(
                self.knowledge_structure
            ),
        )

        object.__setattr__(
            self,
            "metadata",
            deepcopy(
                self.metadata
            ),
        )