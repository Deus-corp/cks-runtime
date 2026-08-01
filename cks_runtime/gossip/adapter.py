"""
GossipAdapter -- applies a remote replica's session state into the
local Runtime by reusing the existing, already-tested MergeOperation
(ADR-007) session-to-session merge path.

ADR-008 status update: the original design in this module attempted
to reconstruct a remote Knowledge Structure by replaying raw
``RuntimeFieldOperation`` rows fetched via ``fetch_operations_since``.
That cannot work as specified: per ``RuntimeFieldOperation``'s own
contract, an ``"add_object"``/``"add_relation"`` entry carries no
payload at all (``field_key``/``field_value`` are always ``None`` for
those op types) -- it only marks that an identity appeared. There is
no way to reconstruct a genuinely new object from the operation log
alone; the log is a field-level accelerant for resolving conflicts on
objects *both* branches already have (exactly how
``MergeOperation._field_level_resolutions`` already uses it), not a
substitute for the actual state.

The fix: gossip exchanges whole ``RuntimeSession`` snapshots (which
already carry a complete ``knowledge_structure``) for a session both
replicas already track, and reconciliation goes through the same
two-phase probe-then-commit sequence cks-mcp's own ``merge_branch``
tool uses -- ``executor.execute(MergeOperation(...))`` to detect a
conflict cheaply with no persisted side effects, then, only on
success, ``begin_transaction`` / ``commit_transaction`` to actually
persist it as a new committed Version. This is not a new merge
mechanism; it is the existing one, reused, exactly as ADR-008's
Decision section intended.

``fetch_operations_since``/``get_or_create_replica_id`` remain useful
-- as a transport-layer accelerant for deciding what's changed and as
a durable peer identity -- but are no longer the payload the merge
itself is built from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import GossipConflictDetected
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import MergeOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_vector import VersionVector

if TYPE_CHECKING:
    from cks_runtime.runtime import Runtime


class GossipAdapter:
    """
    Wraps a ``Runtime`` to apply another replica's session state,
    reconciling it through the existing three-way merge path.

    A single ``GossipAdapter`` is bound to one local replica (one
    ``Runtime``). It knows how to:

    - read the local version vector for a session both replicas track;
    - apply a remote replica's snapshot of that same session, merging
      it into the local session via the standard ``MergeOperation``
      path and persisting the result as a new committed Version;
    - publish ``GossipConflictDetected`` when the merge conflicts,
      instead of raising synchronously -- a background gossip cycle
      has no caller waiting on the call the way a synchronous
      ``merge_branch`` invocation does.

    Bootstrapping a session neither replica has seen before, and the
    actual peer-to-peer transport, are both out of scope here -- see
    ADR-008's Non-Goals; this adapter only reconciles a session that
    already exists locally.
    """

    def __init__(
        self,
        runtime: Runtime,
        replica_id: str,
        event_bus: EventBus | None = None,
    ) -> None:
        self._runtime = runtime
        self._replica_id = replica_id
        self._event_bus = event_bus if event_bus is not None else runtime.events

    @property
    def replica_id(self) -> str:
        return self._replica_id

    # ------------------------------------------------------------------
    # Vector helpers
    # ------------------------------------------------------------------

    async def get_local_vector(self, session_id: str) -> VersionVector:
        """
        Return the local ``VersionVector`` for ``session_id``, or an
        empty vector if this replica doesn't have that session (or it
        has never committed under the ADR-007 scheme).
        """
        session = self._runtime.get_session(session_id)
        if session is None:
            return VersionVector()
        return VersionVector.from_metadata(session.metadata)

    async def get_operations_since(
        self, vector: VersionVector
    ) -> list[RuntimeFieldOperation]:
        """
        Return locally logged operations not yet reflected in
        ``vector`` -- a transport-layer accelerant only (see module
        docstring); not required to apply a remote session.
        """
        storage = self._runtime.storage
        if not getattr(storage, "supports_operation_log", False):
            return []
        return await storage.fetch_operations_since(vector)

    # ------------------------------------------------------------------
    # Apply a remote replica's session snapshot
    # ------------------------------------------------------------------

    async def apply_remote_session(self, remote_session: RuntimeSession) -> bool:
        local = self._runtime.get_session(remote_session.session_id)
        if local is None:
            return False

        local_vector = VersionVector.from_metadata(local.metadata)
        remote_vector = VersionVector.from_metadata(remote_session.metadata)

        if local_vector.dominates(remote_vector):
            return True

        # Fast‑forward: remote dominates → adopt remote state without a
        # full merge, the same way MergeOperation.execute does it.
        if remote_vector.dominates(local_vector):
            local.knowledge_structure = remote_session.knowledge_structure
            local_vector.absorb(remote_vector)
            local_vector.to_metadata(local.metadata)
            # Persist the fast‑forward as a new local Version.
            tx = self._runtime.begin_transaction(local)
            await self._runtime.commit_transaction(tx)
            return True

        # Neither vector dominates -- but if the two sides' actual
        # content is already identical (e.g. neither has committed
        # anything since they started tracking this session_id, so
        # both vectors are still empty), there is nothing to
        # reconcile at all: skip straight to "converged" rather than
        # attempting a merge probe that would fail with "could not
        # determine a merge base" purely because no fork point was
        # ever recorded, even though nothing actually diverged.
        # ``structurally_equivalent`` is an O(1) root-hash comparison
        # (cks.KnowledgeStructure), so this is cheap to check first.
        if local.knowledge_structure.structurally_equivalent(
            remote_session.knowledge_structure
        ):
            return True

        # Neither dominates and content genuinely differs → three‑way
        # merge probe. With no common ancestor MergeOperation can
        # resolve, this deliberately escalates (see
        # ``test_concurrent_divergence_with_no_common_ancestor_is_escalated``)
        # rather than guessing at a merge base.
        def _operation() -> MergeOperation:
            return MergeOperation("gossip-merge", source_session=remote_session)

        probe = await self._runtime.executor.execute(
            _operation(), local, record_metrics=False
        )

        if probe.status == OperationStatus.FAILED:
            if isinstance(probe.error, RuntimeMergeConflictError):
                conflicts = [c.object_id for c in probe.error.conflicts]
            else:
                conflicts = [str(probe.error)]
            if self._event_bus is not None:
                await self._event_bus.publish(
                    GossipConflictDetected(
                        source_replica_id=self._replica_id,
                        conflicts=conflicts,
                    )
                )
            return False

        tx = self._runtime.begin_transaction(local)
        tx.add_operation(_operation())
        await self._runtime.commit_transaction(tx)
        return True