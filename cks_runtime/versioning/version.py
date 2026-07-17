"""
Runtime Version.

Represents an immutable Runtime snapshot.

A RuntimeVersion records the operational state of a
RuntimeSession immediately after a committed RuntimeTransaction.

RuntimeVersions are immutable snapshots.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RuntimeVersion:
    """
    Immutable Runtime snapshot.

    Ownership:

    - belongs to exactly one RuntimeSession;
    - originates from exactly one committed RuntimeTransaction;
    - stores operational state only.

    RuntimeVersion never owns semantic behaviour.
    """

    session_id: str

    transaction_id: str

    knowledge_structure: Any

    metadata: dict[str, Any]

    version_id: str = field(
        default_factory=lambda: str(uuid4()),
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(
            UTC,
        ),
    )

    def __post_init__(self) -> None:
        """
        Deep-copy mutable state.

        RuntimeVersion must never share mutable objects
        with a live RuntimeSession.
        """

        object.__setattr__(
            self,
            "knowledge_structure",
            deepcopy(self.knowledge_structure),
        )

        object.__setattr__(
            self,
            "metadata",
            deepcopy(self.metadata),
        )

    #
    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    #

    @property
    def has_metadata(self) -> bool:
        """
        Whether this RuntimeVersion contains metadata.
        """

        return bool(self.metadata)

    @property
    def age(self):
        """
        Time elapsed since this RuntimeVersion was created.
        """

        return datetime.now(
            UTC,
        ) - self.created_at

    #
    # ------------------------------------------------------------------
    # Debugging
    # ------------------------------------------------------------------
    #

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.version_id!r}, "
            f"session={self.session_id!r})"
        )