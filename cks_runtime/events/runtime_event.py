"""
Runtime Events.

Canonical Runtime Event model.

Runtime Events are immutable.

They describe observable Runtime behaviour.

Events never modify Runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

# ---------------------------------------------------------------------
# Base Event
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """
    Base Runtime Event.

    Every Runtime Event is immutable and contains:

    - unique event identifier;
    - creation timestamp;
    - optional metadata.
    """

    event_id: UUID = field(
        default_factory=uuid4,
    )

    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC),
    )

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )

    @property
    def event_type(self) -> str:
        """
        Canonical Runtime Event type.
        """

        return self.__class__.__name__


# ---------------------------------------------------------------------
# Session Events
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionCreated(RuntimeEvent):
    """
    Runtime Session created.
    """

    session_id: str = ""


@dataclass(frozen=True, slots=True)
class SessionClosed(RuntimeEvent):
    """
    Runtime Session closed.
    """

    session_id: str = ""


# ---------------------------------------------------------------------
# Transaction Events
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransactionCommitted(RuntimeEvent):
    """
    Runtime Transaction committed.
    """

    transaction_id: str = ""

    session_id: str = ""


@dataclass(frozen=True, slots=True)
class TransactionRolledBack(RuntimeEvent):
    """
    Runtime Transaction rolled back.
    """

    transaction_id: str = ""

    session_id: str = ""


@dataclass(frozen=True, slots=True)
class TransactionAborted(RuntimeEvent):
    """
    Runtime Transaction aborted.
    """

    transaction_id: str = ""

    session_id: str = ""


# ---------------------------------------------------------------------
# Version Events
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VersionCreated(RuntimeEvent):
    """
    Runtime Version created.
    """

    version_id: str = ""

    session_id: str = ""

    transaction_id: str = ""


@dataclass(frozen=True, slots=True)
class ValidationFailed(RuntimeEvent):
    """
    Runtime validation failed.
    """

    transaction_id: str = ""
    session_id: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class GossipConflictDetected(RuntimeEvent):
    """
    Published by ``GossipAdapter`` (ADR-008) when merging a remote
    replica's session into the local one raises
    ``RuntimeMergeConflictError``. A background gossip cycle has no
    synchronous caller to raise to, unlike ``merge_branch``, so the
    conflict is escalated here instead -- a subscriber (e.g. a future
    Critic agent) resolves it later through the ordinary
    ``merge_branch`` tool.

    ``session_id`` identifies which of possibly many gossiped sessions
    conflicted -- without it, a subscriber handling this event has no
    way to know which session to pass to ``merge_branch``/
    ``compare_versions`` and can't disambiguate one conflict from
    another when several sessions are gossiping concurrently.
    """

    source_replica_id: str = ""

    session_id: str = ""

    conflicts: list[Any] = field(default_factory=list)