"""
Runtime Version.

Represents an immutable Runtime snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RuntimeVersion:
    """Immutable Runtime snapshot."""

    session_id: str
    transaction_id: str
    knowledge_structure: Any
    metadata: Mapping[str, Any]

    version_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state_hash: str | None = None

    def __post_init__(self) -> None:
        """Deep-copy mutable state and freeze metadata."""
        object.__setattr__(
            self,
            "knowledge_structure",
            deepcopy(self.knowledge_structure),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(deepcopy(dict(self.metadata))),
        )

    #
    # ------------------------------------------------------------------
    # Copy semantics
    # ------------------------------------------------------------------
    #
    # RuntimeVersion.knowledge_structure is `Any`: Runtime is
    # storage-independent and must not assume a plugged-in Core's
    # structure type is immutable, so a blanket `return self` here
    # would silently break the isolation InMemoryStorage relies on
    # for non-cks-core adapters with mutable structures.
    #
    # The only thing the stdlib `copy` module actually can't handle is
    # `metadata`, which we wrap in MappingProxyType (immutable by our
    # own contract) — so we deep-copy every field explicitly instead of
    # deferring to the default pickle-based reduction.

    def __copy__(self) -> "RuntimeVersion":
        return type(self)(
            session_id=self.session_id,
            transaction_id=self.transaction_id,
            knowledge_structure=self.knowledge_structure,
            metadata=dict(self.metadata),
            version_id=self.version_id,
            created_at=self.created_at,
            state_hash=self.state_hash,
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> "RuntimeVersion":
        new = type(self)(
            session_id=self.session_id,
            transaction_id=self.transaction_id,
            knowledge_structure=deepcopy(self.knowledge_structure, memo),
            metadata=dict(self.metadata),
            version_id=self.version_id,
            created_at=self.created_at,
            state_hash=self.state_hash,
        )
        memo[id(self)] = new
        return new

    #
    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def has_metadata(self) -> bool:
        return bool(self.metadata)

    @property
    def age(self):
        return datetime.now(UTC) - self.created_at

    #
    # ------------------------------------------------------------------
    # Debugging
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.version_id!r}, "
            f"session={self.session_id!r})"
        )