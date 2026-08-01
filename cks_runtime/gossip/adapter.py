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

ADR-008 status update (bootstrap): the module and class docstrings
below originally described "bootstrapping a session neither replica
has seen before" as out of scope for this adapter. It no longer is --
see ``_bootstrap_remote_session`` and ``apply_remote_session``'s
``local is None`` branch. There is no local state to reconcile a
never-seen session against, only a remote snapshot to adopt, so this
needed none of the merge machinery above; it reuses the same
"register + persist + commit" sequence the fast-forward path already
uses to turn an adopted snapshot into a real local Version.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from uuid import uuid4

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
    - adopt a session this replica has never tracked before (see
      ``_bootstrap_remote_session``), registering it locally instead
      of reconciling against nonexistent local state;
    - publish ``GossipConflictDetected`` when the merge conflicts,
      instead of raising synchronously -- a background gossip cycle
      has no caller waiting on the call the way a synchronous
      ``merge_branch`` invocation does.
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
            return await self._bootstrap_remote_session(remote_session)

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

    # ------------------------------------------------------------------
    # Bootstrap a session neither replica has seen before
    # ------------------------------------------------------------------

    async def _bootstrap_remote_session(self, remote_session: RuntimeSession) -> bool:
        """
        Adopt ``remote_session`` as a brand-new local session.

        Called only from ``apply_remote_session`` when this replica
        has no local session under ``remote_session.session_id`` at
        all -- there is no local state to reconcile against, so this
        is registration, not merging. Mirrors how
        ``Runtime._restore_from_storage`` registers a session loaded
        from local storage at startup (``SessionManager.restore``),
        except the snapshot originates from a peer instead of this
        replica's own storage backend.

        ``metadata`` (which carries the remote's ``VersionVector``
        under ``version_vector.VERSION_VECTOR_KEY``, per-node-id
        clocks and all) is copied over as-is, so this replica's
        future ``dominates()``/``absorb()`` comparisons already see
        everything the remote had committed before this exchange.
        ``metadata["node_id"]`` is the one exception: it is
        deliberately overwritten with a freshly minted id rather than
        copied from the remote's. ``node_id`` identifies one
        *RuntimeSession instance's* local commits for version-vector
        purposes (ADR-007: "for independent version vectors"), not
        the logical session -- two replicas' RuntimeSession objects
        for the same ``session_id`` must never share one, or a later
        local commit here would silently bump the clock under the
        remote's identity instead of this replica's own. This is the
        same fix ``_paired_replicas`` (the unit test helper) already
        applies by hand when constructing a second replica's session.

        The adoption is committed as a real local Version (an empty
        transaction, same as the fast-forward branch above) rather
        than left as a bare in-memory/storage write, so this
        session's ``version_history``, the storage backend, and any
        ``VersionCreated`` subscriber all observe it exactly as they
        would any other committed state -- there is no
        bootstrap-only code path downstream of this method.
        """
        local = RuntimeSession(
            knowledge_structure=copy.deepcopy(remote_session.knowledge_structure),
            session_id=remote_session.session_id,
            metadata=dict(remote_session.metadata),
            snapshot_interval=remote_session.snapshot_interval,
            parent_session_id=remote_session.parent_session_id,
            parent_version_id=remote_session.parent_version_id,
        )
        local.metadata["node_id"] = str(uuid4())

        self._runtime._sessions.restore(local)
        await self._runtime.storage.save_session(local)

        tx = self._runtime.begin_transaction(local)
        await self._runtime.commit_transaction(tx)
        return True