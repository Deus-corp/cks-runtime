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

    ``source_session_id`` (ADR-008 status update) identifies a
    ``RuntimeSession`` this same ``Runtime`` now tracks, registered via
    ``Runtime.register_foreign_branch`` at the moment this conflict was
    detected, holding the *remote* replica's content that failed to
    merge. Before this field existed, that content was only ever a
    local variable inside ``GossipAdapter.apply_remote_session`` --
    discarded the instant this event was published, with no way for a
    subscriber to later inspect what the remote side actually
    contained. With it, a subscriber can pass
    ``target_session_id=session_id, source_session_id=source_session_id``
    straight to ``merge_branch`` (or ``compare_versions``/
    ``explain_diff`` against it first) to see the real diff, the same
    as resolving any other branch conflict. Empty when this event
    predates that change or (defensively) when registering the branch
    itself failed -- a subscriber should treat an empty
    ``source_session_id`` as "no diff available, only the conflicting
    ids below", not assume it is always populated.
    """

    source_replica_id: str = ""

    session_id: str = ""

    source_session_id: str = ""

    conflicts: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class InferenceConflictDetected(RuntimeEvent):
    """
    Published by ``InferenceStalenessSweeper`` (ADR-009) when a
    background sweep of a recently-modified session finds a
    reasoning-staleness diagnostic (``CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT``
    or ``CKS-EXT-STALE-PREMISE``, see cks-core ADR-001/ADR-002) that
    wasn't already known from a prior sweep of the same session. A
    background sweep has no synchronous caller to raise to, the same
    reason ``GossipConflictDetected`` (ADR-008) is a published event
    rather than a raised exception -- but this is not that event
    repurposed: a reasoning conflict is a single-structure semantic
    condition (ADR-002), not a merge conflict between two replicas'
    operation logs, so ``source_replica_id``/``source_session_id``
    don't apply here and aren't included.

    ``session_id`` identifies which session the finding belongs to.
    ``version_id`` is that session's latest version at the moment the
    finding was made -- a subscriber handling this later can tell
    whether the session has since moved on. ``diagnostics`` carries
    the newly-found entries only (each a ``{"code", "severity",
    "message", "location"}`` dict, the same shape
    ``evolve_knowledge``/``validate_knowledge`` already return) --
    diagnostics the sweeper already reported for this session in an
    earlier sweep are not repeated here.
    """

    session_id: str = ""

    version_id: str = ""

    diagnostics: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AgentStepStarted(RuntimeEvent):
    """
    Published by ``CKSAgentOrchestrator`` (ADR-007, cks-mcp) when an
    ``AgentStep`` (Researcher, Reviewer, Synthesizer, Arbiter, ...)
    begins running against one claimed object. Free observability hook
    for ``cks-dashboard`` -- no new transport, reuses this same
    ``runtime.events`` bus ``SessionCreated``/``GossipConflictDetected``
    already publish on (ADR-007 Decision 5).

    ``step_name`` is the ``AgentStep.name`` that claimed the object;
    ``session_id``/``object_id`` identify what it claimed;
    ``claims_status`` is the ``current_status`` value the step read the
    object out of (see ``cks_mcp.pipeline.schema.PipelineStatus``).
    """

    step_name: str = ""

    session_id: str = ""

    object_id: str = ""

    claims_status: str = ""


@dataclass(frozen=True, slots=True)
class AgentStepCompleted(RuntimeEvent):
    """
    Published by ``CKSAgentOrchestrator`` (ADR-007, cks-mcp) when an
    ``AgentStep`` finishes running against one claimed object,
    successfully or not. ``transitioned_to`` is the resulting
    ``current_status`` on success (empty on failure); ``error`` carries
    the failure detail otherwise -- mirrors the
    ``complete_conflict_task``/``fail_conflict_task`` outcome the
    orchestrator reported for this object's outbox task.
    """

    step_name: str = ""

    session_id: str = ""

    object_id: str = ""

    succeeded: bool = False

    transitioned_to: str = ""

    error: str = ""


@dataclass(frozen=True, slots=True)
class CRDTForkDetected(RuntimeEvent):
    """
    Published by ``GossipAdapter._handle_fork`` (ADR-013, Stage 2) when
    ``CRDTStore.update_pointer`` reports a *concurrent* MV-Register
    write -- two (or more) object_ids for the same ``pointer_key``,
    neither causally dominating the other (see
    ``cks_runtime.crdt.causality.causality_check``). Unlike
    ``GossipConflictDetected``, this is not a session-merge conflict:
    it is a fork in *which object a pointer currently names*, detected
    entirely within the CRDT layer (G-Set + MV-Register), independent
    of any ``RuntimeSession``.

    ``pointer_key`` identifies which MV-Register pointer forked.
    ``conflicting_object_ids`` are every object_id currently competing
    for that pointer (already persisted, alongside their vector
    clocks, in ``cks_conflict_events`` via
    ``CRDTStore.escalate_fork`` -- this event is a live notification
    of that same row, not a substitute for it; a subscriber that
    missed this event can still find the row via
    ``list_pending_forks``). ``conflict_event_id`` is the
    ``cks_conflict_events.event_id`` of that row, so a subscriber can
    call ``mark_fork_resolved`` once it's dealt with, without a
    separate lookup.
    """

    pointer_key: str = ""

    conflicting_object_ids: list[str] = field(default_factory=list)

    conflict_event_id: str = ""


@dataclass(frozen=True, slots=True)
class DuplicateReplicaIdDetected(RuntimeEvent):
    """
    Published by ``GossipAdapter`` when an incoming remote session's
    ``VersionVector`` proves, under *this replica's own* ``replica_id``
    key, that some other process is also committing under this
    replica's identity -- either because the remote's clock for our
    own key is higher than we ourselves have ever recorded (a
    legitimate remote can only ever have *observed* a past-or-current
    value of our own counter via ``absorb()``, never a future one), or
    because it lands on the exact same clock value we hold but with
    genuinely different content (two colliding writers that happened
    to commit the same number of times since a shared genesis land on
    identical counts with different states -- see
    ``_apply_remote_session_locked`` for why an equal count is just as
    conclusive as a higher one here).

    ``VersionVector.bump()`` is only ever called by this replica's own
    commit path, under its own ``replica_id`` -- no legitimate peer
    ever advances another replica's clock. Most commonly this is two
    clones of the same deployment template/image, sharing a
    ``replica_id`` that was baked in rather than generated per-
    installation via ``storage.get_or_create_replica_id()``.

    This is a data-integrity condition, not an ordinary merge
    conflict: ``VersionVector.dominates()``/``absorb()`` treat
    ``{replica_id: int}`` as a one-writer-per-key clock, so two writers
    sharing a key are silently indistinguishable to it -- unlike
    ``GossipConflictDetected``, there is no automatic resolution path
    (``merge_branch`` included) that fixes this, because the vector
    itself no longer means what it's supposed to. ``GossipAdapter``
    refuses to apply (fast-forward, merge, or content-equivalence
    fold) a remote session that trips this check -- see
    ``_apply_remote_session_locked`` -- so this event is the only
    signal an operator gets; resolving it requires giving one of the
    colliding replicas a fresh ``replica_id`` (SPEC-009 Section 4) out
    of band, not anything this Runtime can do for itself.

    ``own_replica_id`` is the colliding identity (this replica's own).
    ``local_clock``/``remote_clock`` are the two disagreeing values --
    either ``remote_clock > local_clock``, or they're equal and the
    two sides' content differs (see this event's own docstring for
    why both cases are equally conclusive). ``session_id`` identifies
    which tracked session's gossip exchange surfaced the collision
    (the same duplicate identity will typically show up again on every
    other session gossiped between these replicas, not just this one).
    """

    session_id: str = ""

    own_replica_id: str = ""

    local_clock: int = 0

    remote_clock: int = 0