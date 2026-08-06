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
see ``_bootstrap_remote_session`` and
``_apply_remote_session_locked``'s ``local is None`` branch. There is
no local state to reconcile a
never-seen session against, only a remote snapshot to adopt, so this
needed none of the merge machinery above; it reuses the same
"register + persist + commit" sequence the fast-forward path already
uses to turn an adopted snapshot into a real local Version.
"""

from __future__ import annotations

import asyncio
import copy
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.crdt.quarantine import CRDTQuarantine, _SupportsAddObject
from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import CRDTForkDetected, GossipConflictDetected
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import (
    EMPTY_STATE_VERSION_ID,
    MergeOperation,
)
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_vector import VersionVector

if TYPE_CHECKING:
    from cks_runtime.runtime import Runtime

if TYPE_CHECKING:
    from cks_runtime.crdt.crdt_store import (
        InMemoryCRDTStore,
        PostgresCRDTStore,
        SQLiteCRDTStore,
    )
    _CrdtStore = SQLiteCRDTStore | InMemoryCRDTStore | PostgresCRDTStore
else:
    _CrdtStore = object  # для рантайма


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
      ``merge_branch`` invocation does. The remote content that failed
      to merge is first materialized as a real local branch (see
      ``_register_conflict_branch``), so the event's
      ``source_session_id`` gives a subscriber something to diff
      against instead of only a bare list of conflicting ids. A
      conflict already reported for the same remote content on a prior
      gossip round is not re-registered or re-published (see
      ``_pending_conflict_vectors``);
    - serialize concurrent ``apply_remote_session`` calls that target
      the same ``session_id`` (see ``_lock_for``), so two inbound
      gossip requests for one session arriving at the same time
      reconcile one after the other instead of racing each other's
      transaction. Calls for *different* sessions are never blocked
      on each other.
    """

    def __init__(
        self,
        runtime: Runtime,
        replica_id: str,
        event_bus: EventBus | None = None,
        crdt_store: _CrdtStore | None = None,
    ) -> None:
        self._runtime = runtime
        self._replica_id = replica_id
        self._event_bus = event_bus if event_bus is not None else runtime.events
        # ADR-013 Stage 1: optional G-Set of KnowledgeObjects, kept in
        # sync alongside (not instead of) the ADR-007/ADR-008 session
        # merge below. `None` by default -- a GossipAdapter built
        # without a `crdt_store` behaves exactly as before this
        # module changed; `_merge_crdt_objects` becomes a no-op.
        # Duck-typed rather than importing a concrete crdt_store type
        # here, so both the sync (`SQLiteCRDTStore`/`InMemoryCRDTStore`)
        # and async (`PostgresCRDTStore`) backends work without this
        # module needing to special-case either.
        self._crdt_store = crdt_store
        # ADR-013 Stage 2: gate every object this replica ever admits
        # into `crdt_store` through CRDTQuarantine -- structural
        # validity (`runtime.core_bridge.validate`) plus a recomputed
        # identity check (`object_id_for`, see crdt_store.py) -- before
        # `_merge_crdt_objects` is allowed to add it. Built lazily,
        # once, alongside `crdt_store` itself: a GossipAdapter with no
        # `crdt_store` still has nothing to quarantine (same as
        # before), and one *with* a `crdt_store` now always merges
        # through quarantine rather than optionally, so a remote peer
        # can no longer poison the Merkle tree / MV-Register with a
        # payload that fails `cks.validate()` or whose claimed id
        # doesn't match its own content -- see the module's ADR-013
        # Stage 2 history for why this was previously wired up but
        # never actually reachable from the gossip path.
        self._quarantine = (
            CRDTQuarantine(cast(_SupportsAddObject, crdt_store), runtime.core_bridge)
            if crdt_store is not None
            else None
        )
        # session_id -> lock serializing apply_remote_session for that
        # session (see _lock_for below). Deliberately per-session, not
        # one lock for the whole adapter.
        self._session_locks: dict[str, asyncio.Lock] = {}
        # session_id -> the remote VersionVector that most recently
        # triggered an *unresolved* conflict for that session (see
        # _register_conflict_branch). A gossip cycle runs on a fixed
        # interval (PeerScheduler, default every few seconds) and keeps
        # retrying every tracked session regardless of whether its last
        # attempt conflicted -- so as long as a conflict sits
        # unaddressed, the *same* remote content arrives again on the
        # very next round. Without this, every one of those retries
        # would call register_foreign_branch again and leak one more
        # full RuntimeSession (unlike ConflictInbox's own records,
        # SessionManager's registry is not ring-buffer capped) and
        # re-publish GossipConflictDetected for content a Critic agent
        # has already been told about. Cleared whenever
        # _apply_remote_session_locked resolves this session_id by any
        # path (converged, fast-forwarded, or merged) -- see its own
        # `return True` sites -- so a *new* conflict on the same
        # session_id after that always registers fresh.
        self._pending_conflict_vectors: dict[str, VersionVector] = {}

    @property
    def replica_id(self) -> str:
        return self._replica_id

    @staticmethod
    def anchor_genesis(session: RuntimeSession) -> None:
        """
        Anchor a freshly-created, not-yet-gossiped session to the
        well-known empty state (``EMPTY_STATE_VERSION_ID``) as its
        recorded fork point.

        Call this once, right after ``Runtime.create_session()``, on
        whichever replica is the true origin of a session meant to be
        gossiped -- i.e. the one call in the whole deployment that
        does *not* go through ``_bootstrap_remote_session`` (that path
        already anchors to the same constant automatically for every
        other replica that later adopts this session_id via gossip).
        Without this call, the origin's own ``parent_version_id`` stays
        ``None``, and any peer that later merges this session in as a
        ``source_session`` hits "could not determine a merge base"
        despite every *other* replica converging fine.

        Idempotent and safe to call even on a session that will never
        be gossiped -- it only changes what a future ``MergeOperation``
        would resolve as this session's fork point, nothing about its
        current content. Not persisted automatically; if the session's
        storage backend needs the change durable before the next
        commit, save it explicitly (``await runtime.storage.save_session(session)``).
        """

        session.parent_version_id = EMPTY_STATE_VERSION_ID

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

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        """
        Return the ``asyncio.Lock`` serializing ``apply_remote_session``
        calls for one ``session_id``.

        ``apply_remote_session`` reads the local session, decides how
        to reconcile it, and only *then* opens a
        ``begin_transaction``/``commit_transaction`` pair around the
        result -- several ``await`` points with nothing enforcing
        atomicity between them. Two inbound gossip requests for the
        same ``session_id`` arriving concurrently (the ordinary shape
        of a multi-peer mesh under load, not an edge case -- ADR-008)
        can both pass ``TransactionManager.begin``'s
        "no active transaction yet" check before either commits, and
        the second call raises ``RuntimeError("Session already has an
        active transaction.")``. That failure isn't fatal -- the
        losing round is simply dropped and the next gossip round
        converges as usual -- but it is pure noise and a wasted round
        under any real concurrent load.

        Locking is per-``session_id``, not global: unrelated sessions
        must still reconcile fully concurrently (one gossip node
        commonly tracks many sessions shared with many peers at once),
        so a single lock across every session would serialize far more
        than the actual race requires.

        Lookup and insertion below are synchronous with no ``await``
        between them, so this is race-free on its own even though nothing
        else in ``GossipAdapter`` synchronizes access to this dict --
        the surrounding event loop is single-threaded (see
        ``scheduling.py``), so nothing can interleave between the
        membership check and the assignment.

        Locks are created lazily and kept for the adapter's lifetime,
        one per distinct ``session_id`` ever seen -- the same policy
        this package already applies to session state itself
        (``Runtime`` keeps every session it has ever tracked), so this
        adds no new unbounded-growth concern beyond what already
        exists.
        """
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    # ------------------------------------------------------------------
    # Apply a remote replica's session snapshot
    # ------------------------------------------------------------------

    async def apply_remote_session(self, remote_session: RuntimeSession) -> bool:
        """
        Apply ``remote_session`` into the local ``Runtime``, serialized
        per ``session_id`` (see ``_lock_for``) against any other
        concurrent ``apply_remote_session`` call for the same session --
        this method itself does the reconciliation, unlocked, in
        ``_apply_remote_session_locked``.
        """
        async with self._lock_for(remote_session.session_id):
            return await self._apply_remote_session_locked(remote_session)

    async def _merge_crdt_objects(self, remote_session: RuntimeSession) -> int:
        """
        Add every KnowledgeObject in ``remote_session`` into the local
        CRDT G-Set (ADR-013 Stage 1), if a ``crdt_store`` was
        configured. Returns how many were new.

        This is deliberately unconditional and side-effect-only: it
        never reads or depends on version vectors, merge outcomes, or
        conflicts -- a G-Set only grows, so every object this replica
        has ever seen (from any remote session snapshot, converged,
        fast-forwarded, or later found to conflict at the session
        level) belongs in it. Running this before the ordinary session
        merge/conflict handling below means the G-Set is complete even
        when the *session* reconciliation reports a conflict and
        leaves ``local`` untouched.
        """
        if self._crdt_store is None:
            return 0
        objects = remote_session.knowledge_structure.objects
        # Every object gossiped in from a peer goes through
        # CRDTQuarantine first (structural validity + a recomputed
        # identity check), not straight into `crdt_store.merge_objects`
        # -- see `self._quarantine`'s construction in `__init__`.
        # `_quarantine` is only ever None when `_crdt_store` is also
        # None (guarded above), so this assert documents that
        # invariant for readers rather than silently no-op'ing if it
        # were ever violated.
        assert self._quarantine is not None
        result = await self._quarantine.process_batch(list(objects))

        # ADR-013 Stage 2: also advance each object's identity-scoped
        # MV-Register pointer (`identity.id`, the application-level
        # object identity -- distinct from the G-Set's Merkle-hash
        # `object_id`) to this node's remote vector clock, escalating
        # to `_handle_fork` whenever `update_pointer` reports a
        # concurrent write. Best-effort: a store without MV-Register
        # support (or a duck-typed test double) is skipped silently,
        # since this is additive to the Stage 1 G-Set merge above, not
        # a replacement for it.
        update_pointer = getattr(self._crdt_store, "update_pointer", None)
        if callable(update_pointer):
            from cks_runtime.crdt.crdt_store import object_id_for
            from cks_runtime.crdt.version_vector import (
                VersionVector as CrdtVersionVector,
            )

            remote_vv = CrdtVersionVector.from_dict(
                remote_session.metadata.get("crdt_version_vector")
            )
            for obj in objects:
                identity = getattr(obj, "identity", None)
                pointer_key = getattr(identity, "id", None)
                if not pointer_key:
                    continue
                try:
                    object_id = object_id_for(obj)
                except TypeError:
                    continue
                added = update_pointer(pointer_key, object_id, remote_vv, self._replica_id)
                if hasattr(added, "__await__"):
                    added = await added
                if added:
                    await self._detect_and_handle_fork(pointer_key)

        return result

    async def _detect_and_handle_fork(self, pointer_key: str) -> None:
        """
        After a successful ``update_pointer`` write, check whether
        ``pointer_key`` now has more than one live pointer (a fork)
        and, if so, hand it to ``_handle_fork``. Kept as a separate
        step rather than inline in ``_merge_crdt_objects`` so tests can
        call it directly against a store pre-seeded with concurrent
        pointers, without needing a full remote session round-trip.
        """
        get_pointers = getattr(self._crdt_store, "get_pointers", None)
        if not callable(get_pointers):
            return
        pointers = get_pointers(pointer_key)
        if hasattr(pointers, "__await__"):
            pointers = await pointers
        if len(pointers) <= 1:
            return
        object_ids = [p["object_id"] for p in pointers]
        vector_clocks = [p["vector_clock"] for p in pointers]
        await self._handle_fork(pointer_key, object_ids, vector_clocks)

    async def _handle_fork(
        self,
        pointer_key: str,
        object_ids: list[str],
        vector_clocks: list[dict[str, int]],
    ) -> None:
        """
        Escalate a detected MV-Register fork (ADR-013, Stage 2): persist
        it via ``CRDTStore.escalate_fork`` (which also sends `NOTIFY
        cks_fork_detected` for the Postgres backend) and publish
        ``CRDTForkDetected`` on this Runtime's event bus for any
        in-process subscriber (mirroring how ``GossipConflictDetected``
        is published below for session-level conflicts).
        """
        escalate_fork = getattr(self._crdt_store, "escalate_fork", None)
        if not callable(escalate_fork):
            return
        event_id = escalate_fork(pointer_key, object_ids, vector_clocks)
        if hasattr(event_id, "__await__"):
            event_id = await event_id

        if self._event_bus is not None:
            await self._event_bus.publish(
                CRDTForkDetected(
                    pointer_key=pointer_key,
                    conflicting_object_ids=object_ids,
                    conflict_event_id=event_id,
                )
            )

    async def _apply_remote_session_locked(self, remote_session: RuntimeSession) -> bool:
        await self._merge_crdt_objects(remote_session)

        local = self._runtime.get_session(remote_session.session_id)
        if local is None:
            return await self._bootstrap_remote_session(remote_session)

        local_vector = VersionVector.from_metadata(local.metadata)
        remote_vector = VersionVector.from_metadata(remote_session.metadata)

        if local_vector.dominates(remote_vector):
            self._pending_conflict_vectors.pop(remote_session.session_id, None)
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
            self._pending_conflict_vectors.pop(remote_session.session_id, None)
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
            self._pending_conflict_vectors.pop(remote_session.session_id, None)
            return True

        # Neither dominates and content genuinely differs → three‑way
        # merge probe. If both sides' parent_version_id happen to
        # agree on a resolvable common ancestor -- most commonly
        # EMPTY_STATE_VERSION_ID, when both were anchored via
        # anchor_genesis()/_bootstrap_remote_session -- MergeOperation
        # resolves it and this merges cleanly, no escalation. With no
        # such anchor at all (see
        # ``test_concurrent_divergence_with_no_common_ancestor_is_escalated``)
        # it deliberately escalates rather than guessing at a merge
        # base.
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

            # Same remote content that already triggered an unresolved
            # conflict for this session_id (see _pending_conflict_vectors)
            # -- a retried gossip round for content a Critic agent has
            # already been told about, not new information. Skip
            # re-registering a branch and re-publishing the event; the
            # method still reports the conflict as unresolved.
            already_pending = self._pending_conflict_vectors.get(
                remote_session.session_id
            )
            if already_pending == remote_vector:
                return False

            source_session_id = await self._register_conflict_branch(
                local, remote_session
            )
            self._pending_conflict_vectors[remote_session.session_id] = remote_vector

            if self._event_bus is not None:
                await self._event_bus.publish(
                    GossipConflictDetected(
                        source_replica_id=self._replica_id,
                        session_id=local.session_id,
                        source_session_id=source_session_id,
                        conflicts=conflicts,
                    )
                )
            return False

        tx = self._runtime.begin_transaction(local)
        tx.add_operation(_operation())
        await self._runtime.commit_transaction(tx)
        self._pending_conflict_vectors.pop(remote_session.session_id, None)
        return True

    async def _register_conflict_branch(
        self, local: RuntimeSession, remote_session: RuntimeSession
    ) -> str:
        """
        Materialize ``remote_session``'s content as a local branch of
        ``local`` (via ``Runtime.register_foreign_branch``) at the
        moment a gossip merge conflict is detected, so
        ``GossipConflictDetected.source_session_id`` gives a subscriber
        something real to diff against -- ``merge_branch``/
        ``compare_versions``/``explain_diff`` against ``local.session_id``
        -- instead of only the bare list of conflicting object ids.

        ``parent_version_id`` is passed through as
        ``remote_session.parent_version_id`` unchanged: that is exactly
        the base the just-failed merge probe attempted to resolve
        against, so a later ``merge_branch`` retried by hand from this
        branch resolves the same fork point, not a recomputed one.

        Defensive: registration failure (e.g. a storage hiccup) must
        never swallow the conflict escalation itself -- an empty
        ``source_session_id`` ("no diff available, only the conflicting
        ids") is far better than losing the event entirely. See
        ``GossipConflictDetected``'s own docstring for that contract.
        """
        try:
            branch = await self._runtime.register_foreign_branch(
                local,
                remote_session.knowledge_structure,
                parent_version_id=remote_session.parent_version_id,
                metadata={"gossip_source_replica_id": self._replica_id},
            )
        except Exception:  # noqa: BLE001 -- must not lose the conflict escalation itself
            return ""
        return branch.session_id

    # ------------------------------------------------------------------
    # Bootstrap a session neither replica has seen before
    # ------------------------------------------------------------------

    async def _bootstrap_remote_session(self, remote_session: RuntimeSession) -> bool:
        """
        Adopt ``remote_session`` as a brand-new local session.

        Called only from ``_apply_remote_session_locked`` (itself only
        reachable through the public ``apply_remote_session``, under
        that session's lock) when this replica has no local session
        under ``remote_session.session_id`` at all -- there is no
        local state to reconcile against, so this is registration,
        not merging. Mirrors how
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
            # Deliberately EMPTY_STATE_VERSION_ID, not
            # remote_session.parent_version_id: whatever the remote's
            # own recorded fork point was, it lives in the remote's
            # version_history, which this replica has never seen and
            # never will (gossip carries snapshots, not history). From
            # *this* replica's point of view there is genuinely nothing
            # before this bootstrap commit, so its own fork point is
            # the well-known empty state -- see EMPTY_STATE_VERSION_ID
            # and anchor_genesis(), which does the equivalent for a
            # session's true origin (created locally, not bootstrapped).
            parent_version_id=EMPTY_STATE_VERSION_ID,
        )
        local.metadata["node_id"] = str(uuid4())

        self._runtime._sessions.restore(local)
        await self._runtime.storage.save_session(local)

        tx = self._runtime.begin_transaction(local)
        await self._runtime.commit_transaction(tx)
        return True