"""
Inference Staleness Sweeper (ADR-009).

Periodically re-validates recently-modified sessions against the two
opt-in reasoning constraints cks-core's ADR-001/ADR-002 already ship
but nothing in cks-runtime ever runs proactively:

- ``inference_confidence_conflict`` (``CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT``) --
  two active ``InferenceStep``s sharing a conclusion but disagreeing on
  confidence.
- ``stale_premise`` (``CKS-EXT-STALE-PREMISE``) -- an active
  ``InferenceStep`` whose premises still cite another ``InferenceStep``
  that has since been superseded.

Today both are only ever surfaced when an agent explicitly calls
``validate_knowledge``/``detect_contradictions`` with the extension
opted in. A session that accumulates a conflict through gossip-merged
or independently-evolved branches, and is never re-validated by hand,
sits on an unresolved reasoning conflict indefinitely. This sweeper
closes that gap by finding it proactively and publishing
``InferenceConflictDetected`` (see ``events/runtime_event.py``) for a
subscriber -- e.g. a future Critic agent driving
``arbitrate_inference_conflict`` -- to act on, the same escalation
shape ``GossipAdapter`` already uses for merge conflicts (ADR-008).

Design notes
------------
* Runs as an ``asyncio.Task``, same model as ``GarbageCollector`` and
  ``OutboxEmbeddingWorker``. Start it with ``await sweeper.start()``
  inside a running event loop, stop it with ``await sweeper.stop()``.

* Only active when the storage backend exposes
  ``list_sessions_modified_since``. Plain ``InMemoryStorage`` inherits
  the base no-op default (always returns ``[]``), so the worker task
  still runs but every sweep is a no-op -- same convention
  ``GarbageCollector`` already follows for ``list_sessions_modified_before``.

* Unlike GC's cutoff, a session is **never** excluded here for being
  open or closed -- an actively-edited session is exactly where a
  fresh reasoning conflict is most likely to appear, so ``closed`` is
  not consulted at all.

* Detection calls ``cks.validate`` directly with only the two
  reasoning-staleness constraints opted in via ``extra_constraints``,
  the same "opt in the specific extension, call cks.validate directly"
  idiom ``cks-mcp``'s ``suggest_evolution`` preview path already uses
  (see ``EXTENSION_ALIASES``/``OPTIONAL_CONSTRAINTS_BY_NAME`` there) --
  not through ``Runtime.core_bridge``, which is the commit-pipeline's
  boundary, not a free-standing query's. ``cks-runtime`` already
  depends on ``cks`` directly for (de)serialization (see
  ``sqlite_storage.py``/``postgres_storage.py``), so this adds no new
  coupling.

* A diagnostic already published for a session on a prior sweep is not
  re-published on a later one that still finds it (see
  ``_known_diagnostics``) -- only genuinely new ``(code, location)``
  pairs are reported. This mirrors ``GossipAdapter``'s own
  ``_pending_conflict_vectors`` dedup, for the same reason: a sweep
  interval is far shorter than how long a real conflict typically
  stays unresolved, so without this a subscriber would be re-told
  about the same finding on every single sweep.

* The watermark only advances past a sweep once that sweep's query
  came back under its requested limit -- i.e. once it has genuinely
  drained everything currently at or after the old watermark. A
  session's ``modified_at`` is a storage-only column (not carried on
  the returned ``RuntimeSession`` itself), so there is no per-item
  timestamp to page on the way ``list_sessions_modified_before``-based
  GC batching can lean on eviction shrinking the pool between sweeps.
  Instead, one sweep re-queries with a growing limit (see
  ``_MAX_SWEEP_LIMIT``) until the result is provably complete for that
  watermark, then advances to the wall-clock time the sweep began
  (not "now" at the end, so a session modified while the sweep itself
  was running is picked up next time rather than skipped).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import cks
from cks.constraints.builtin import OPTIONAL_CONSTRAINTS_BY_NAME

from cks_runtime.events.runtime_event import InferenceConflictDetected
from cks_runtime.reasoning.sweeper_status import SweeperStatusMixin

if TYPE_CHECKING:
    from cks_runtime.events.event_bus import EventBus
    from cks_runtime.session.session import RuntimeSession
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

logger = logging.getLogger(__name__)

# Sentinel: attribute name used to duck-type sweep-capable storage.
# Mirrors GarbageCollector's own _GC_METHODS convention.
_SWEEP_METHODS = ("list_sessions_modified_since",)

# The two ADR-001/ADR-002 reasoning-staleness constraints this sweeper
# checks for -- deliberately not the other three reasoning extensions
# (referential integrity, confidence bounds, supersession chain):
# those are structural ERRORs already caught the moment an agent
# evolves a session through the ordinary validate/evolve path, not
# the kind of silently-accumulating belief conflict this sweeper
# exists to surface.
_RELEVANT_CONSTRAINT_NAMES = ("inference_confidence_conflict", "stale_premise")

# Upper bound on how large a single sweep's re-query can grow while
# draining a backlog (see module docstring) -- a safety valve against
# one sweep monopolising the event loop indefinitely on a pathological
# backlog, not a limit expected to be hit in ordinary operation.
_MAX_SWEEP_LIMIT = 100_000


def _storage_supports_sweep(storage: object) -> bool:
    return all(callable(getattr(storage, m, None)) for m in _SWEEP_METHODS)


class InferenceStalenessSweeper(SweeperStatusMixin):
    """
    Background worker that proactively re-checks recently-modified
    sessions for reasoning-staleness diagnostics and publishes
    ``InferenceConflictDetected`` for newly-found ones.

    Parameters
    ----------
    storage:
        The runtime storage backend. The sweeper is a no-op when the
        backend does not implement ``list_sessions_modified_since``
        (e.g. ``InMemoryStorage``).
    event_bus:
        Where ``InferenceConflictDetected`` is published. Required --
        publishing findings is this worker's entire purpose, unlike
        ``GossipAdapter``'s optional event bus (which can run with
        gossip-conflict escalation disabled).
    sweep_interval:
        How often the sweep loop wakes up. Defaults to 5 minutes --
        tighter than GC's 10, since a reasoning conflict left
        unsurfaced is an epistemic risk (an agent may keep building on
        a belief that already disagrees with itself), not merely
        storage bloat.
    batch_size:
        Initial page size per storage query within a sweep. Grown
        automatically (see module docstring) if a sweep's backlog
        exceeds it, up to ``_MAX_SWEEP_LIMIT``.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        event_bus: EventBus,
        *,
        sweep_interval: float = 300.0,  # 5 minutes
        batch_size: int = 100,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._sweep_interval = sweep_interval
        self._batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Sessions modified at or after this instant are candidates
        # for the next sweep. Starts at the Unix epoch so the very
        # first sweep after startup considers every existing session
        # at least once.
        self._watermark: datetime = datetime.fromtimestamp(0, tz=UTC)

        self._init_sweeper_status()

        # session_id -> the set of (code, location) pairs already
        # published for that session, across all sweeps so far. A
        # sweep that finds the exact same still-unresolved diagnostic
        # again (the session hasn't changed enough to clear it, or it
        # keeps re-entering the modified_at window on ties at the
        # watermark boundary -- list_sessions_modified_since's
        # comparison is inclusive) does not re-publish. Not capped or
        # evicted -- same accepted unbounded-growth tradeoff
        # GossipAdapter's own per-session dict already makes (see its
        # _pending_conflict_vectors docstring) for the same reason:
        # bounding it usefully would need knowing which sessions are
        # gone for good, which storage doesn't tell this worker.
        self._known_diagnostics: dict[str, set[tuple[str, str | None]]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        async with self._control_lock:
            if self._running:
                return
            if not _storage_supports_sweep(self._storage):
                logger.info(
                    "%s does not support sweep methods; "
                    "InferenceStalenessSweeper will not start.",
                    type(self._storage).__name__,
                )
                return
            self._running = True
            self._task = asyncio.create_task(self._run(), name="cks-inference-sweep")
            logger.info(
                "InferenceStalenessSweeper started "
                "(sweep_interval=%.0fs, batch=%d).",
                self._sweep_interval,
                self._batch_size,
            )

    async def stop(self) -> None:
        async with self._control_lock:
            self._running = False
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            logger.info("InferenceStalenessSweeper stopped.")

    # ------------------------------------------------------------------
    # Sweep loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            started_at = datetime.now(UTC)
            try:
                result = await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_sweep_error(started_at, exc)
                logger.exception(
                    "InferenceStalenessSweeper sweep failed; "
                    "will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._sweep_interval)
            desired = self._storage.get_sweeper_desired_running("inference_staleness")
            # get_sweeper_desired_running may be sync (SQLiteStorage) or
            # async (PostgresStorage/StorageAdapter).
            if asyncio.iscoroutine(desired):
                desired = await desired
            if desired is False:
                self._running = False
                break

    async def _sweep(self) -> list[Any]:
        sweep_started_at = datetime.now(UTC)
        watermark = self._watermark
        list_fn = self._storage.list_sessions_modified_since

        limit = self._batch_size
        candidates: list[Any] = []

        while True:
            result = list_fn(watermark, limit)
            # list_fn may be sync (SQLiteStorage) or async (PostgresStorage).
            batch = await result if asyncio.iscoroutine(result) else result

            if len(batch) < limit:
                # This query, at this limit, returned everything at or
                # after `watermark` -- the backlog from this watermark
                # is fully drained. `batch` supersedes any earlier,
                # smaller-limit pass of this same sweep (deterministic
                # oldest-first ordering makes it a superset), so it's
                # the complete candidate set to process.
                candidates = batch
                self._watermark = sweep_started_at
                break

            if limit >= _MAX_SWEEP_LIMIT:
                # Safety valve: an unreasonably large backlog. Process
                # this capped batch and leave the watermark where it
                # is -- the next sweep picks up the remainder, same as
                # GC's own per-sweep batch_size cap.
                candidates = batch
                break

            # Full page: more candidates may exist past this page's
            # limit. Re-query with a larger limit from the same
            # watermark -- deterministic oldest-first ordering means
            # the next pass is always a superset of this one, so the
            # smaller batch is simply discarded rather than merged.
            limit *= 4

        for session in candidates:
            await self._sweep_session(session)

        # `candidates` here is "sessions considered this sweep", not
        # "conflicts escalated" (unlike the other sweepers' sweep_once) --
        # _sweep_session doesn't report a per-session escalation count.
        # Still a meaningful liveness signal for agent_status: 0 means
        # the sweep ran and found nothing due, a number means it's
        # actively processing a backlog.
        return candidates

    async def _sweep_session(self, session: RuntimeSession) -> None:
        structure = session.knowledge_structure
        if structure is None:
            return

        constraints = [
            OPTIONAL_CONSTRAINTS_BY_NAME[name] for name in _RELEVANT_CONSTRAINT_NAMES
        ]
        relevant_codes = {c.identity for c in constraints}

        result = cks.validate(structure, extra_constraints=constraints)
        relevant_diagnostics = [
            d for d in result.diagnostics if d.identity in relevant_codes
        ]

        current = {(d.identity, d.location) for d in relevant_diagnostics}
        known = self._known_diagnostics.get(session.session_id, set())
        new_pairs = current - known

        if new_pairs:
            new_diagnostics = [
                {
                    "code": d.identity,
                    "severity": d.severity.value,
                    "message": d.message,
                    "location": d.location,
                }
                for d in relevant_diagnostics
                if (d.identity, d.location) in new_pairs
            ]
            latest_version = session.version_history[-1] if session.version_history else None
            await self._event_bus.publish(
                InferenceConflictDetected(
                    session_id=session.session_id,
                    version_id=latest_version.version_id if latest_version else "",
                    diagnostics=new_diagnostics,
                )
            )

        self._known_diagnostics[session.session_id] = current

    # ------------------------------------------------------------------
    # Convenience: run a single sweep synchronously (useful in tests)
    # ------------------------------------------------------------------

    async def run_once(self) -> list[Any]:
        """Trigger one sweep immediately, without starting the background loop.

        Unlike the ``_run()`` loop, a raised exception propagates to the
        caller rather than being swallowed -- ``run_once`` is used by
        tests and manual triggers that want to see the failure, not a
        long-running background worker that should keep going. Status
        (``last_run_at``/``last_error``/etc, see ``status()``) is
        recorded either way.
        """
        started_at = datetime.now(UTC)
        try:
            result = await self._sweep()
        except Exception as exc:
            self._record_sweep_error(started_at, exc)
            raise
        self._record_sweep_success(started_at, result)
        return result

    # ------------------------------------------------------------------
    # Status (agent_status / list_agents, see cks-mcp)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self.sweeper_status(
            agent_id="inference_staleness",
            running=self._running,
            interval_seconds=self._sweep_interval,
        )