"""
GossipAdapter – applies remote operations received from another replica.

ADR-008: This is the active component that merges incoming operations
into the local knowledge base, using the existing MergeOperation to
reuse conflict detection and resolution logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.versioning.version_vector import VersionVector


@dataclass
class GossipConflictDetected:
    source_replica_id: str
    conflicts: list[str]


class GossipAdapter:
    """
    Wraps a Runtime's storage and core bridge to apply operations
    received from another replica.

    A single GossipAdapter is bound to one local replica. It knows
    how to:

    - retrieve the local version vector from storage metadata
    - query the operation log for operations since a given vector
    - apply a batch of remote operations, merging them into the
      local session and updating the local vector
    - publish ``GossipConflictDetected`` when a merge conflict occurs
    """

    def __init__(
        self,
        storage: Any,            # AsyncRuntimeStorage (or sync via adapter)
        core_bridge: Any,
        replica_id: str,
        event_bus: Any | None = None,
    ) -> None:
        self._storage = storage
        self._core_bridge = core_bridge
        self._replica_id = replica_id
        self._event_bus = event_bus


    @property
    def replica_id(self) -> str:
        return self._replica_id

    # ------------------------------------------------------------------
    # Vector helpers
    # ------------------------------------------------------------------

    async def get_local_vector(self) -> VersionVector:
        """
        Return the current version vector from the local replica's metadata.
        The vector is stored in the *replica* identity, not per session,
        so we read it from a well-known key in the storage's identity table
        (get_or_create_replica_id stores it there, but we need the vector
        separately).
        """
        # For now, we store the vector in a dedicated key alongside replica_id.
        # The storage layer doesn't expose a generic key-value store; we'll
        # retrieve the latest vector by scanning session vectors? Wait,
        # the version vector is per-session, not per-replica. In our current
        # design, each session has its own version vector tracking the
        # highest commit clock from each node that has written to it.
        # For gossip, we need a *replica-level* vector that aggregates
        # all sessions. For simplicity, we'll use the session's vector
        # of a designated "gossip session" (like a global session).
        # But the user hasn't implemented that yet. So for now, we'll
        # assume the adapter is given the vector externally, or we use
        # a dummy vector. We'll refine later.
        # Placeholder: return an empty vector (will be replaced later).
        return VersionVector()

    async def get_operations_since(
        self, vector: VersionVector
    ) -> list[RuntimeFieldOperation]:
        """Return operations from the local log that are not yet reflected in *vector*."""
        return await self._storage.fetch_operations_since(vector)

    # ------------------------------------------------------------------
    # Merge incoming remote operations
    # ------------------------------------------------------------------

    async def apply_remote_operations(
        self,
        source_replica_id: str,
        operations: list[RuntimeFieldOperation],
        source_vector: VersionVector,
    ) -> bool:
        """Apply a batch of operations received from a remote replica.

        Creates a temporary gossip session, synthesises a Knowledge
        Structure from the remote operations using the Core's
        ``synthesize_merge``, and merges it into the local state via
        the standard ``MergeOperation`` path.

        Returns True if all operations were applied without conflict,
        False if one or more conflicts were detected and escalated
        via ``GossipConflictDetected``.
        """

        if not operations:
            return True

        # Build a synthetic Knowledge Structure from the remote operations.
        # We rely on the Core's synthesize_merge to create the merged object,
        # then wrap it in a session for MergeOperation.
        # For the first version, we need a base object — use the first
        # operation's object_id and the local session's current state.
        # This is a simplified approach; a full implementation would
        # batch operations per object_id and apply them sequentially.
        try:
            # Placeholder: create a fresh session with an empty structure
            # and let MergeOperation handle the rest. In practice, the
            # gossip session should already exist and be passed in.

            # Merge the remote operations into the local gossip session.
            # Since MergeOperation expects a source_session with a
            # Knowledge Structure, we create one from the first operation's
            # object_id. A full implementation would reconstruct the
            # entire remote structure from the operation log.

            # Execute the merge. If it fails with conflict, escalate.
            # Note: executor is not available here; we need to refactor
            # to accept it. For now, raise NotImplementedError.
            raise NotImplementedError(
                "Full apply_remote_operations requires access to OperationExecutor"
            )
        except NotImplementedError:
            raise
        except Exception as exc: # noqa: BLE001
            if self._event_bus is not None:
                await self._event_bus.publish(
                    GossipConflictDetected(
                        source_replica_id=source_replica_id,
                        conflicts=[str(exc)],
                    )
                )
            return False