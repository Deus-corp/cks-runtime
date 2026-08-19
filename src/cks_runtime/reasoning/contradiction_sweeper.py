"""
ContradictionSweeper: background detection of logical contradictions
declared via ``MutualExclusionRule``/``FunctionalRelationRule`` objects
(cks-core's contradiction extension, ``cks/constraints/contradiction.py``).

Structural counterpart to InferenceStalenessSweeper/ProvenanceStalenessSweeper/
TemporalStalenessSweeper. Where cks-mcp's ``detect_contradictions`` tool only
ever runs when an agent explicitly calls it for one session, this sweeper
periodically re-checks every recently-modified session so a contradiction
introduced by a gossip-merged or independently-evolved branch -- and never
re-validated by hand -- doesn't sit unresolved indefinitely. Newly-found
contradictions are escalated as ``contradiction_detected`` outbox tasks, the
same persistent-outbox machinery ``ProvenanceStalenessSweeper``/
``TemporalStalenessSweeper`` already use (see ADR-010/ADR-011), for
cks-mcp's ``critic_agent`` to pick up via ``resolve_contradiction``.

Implementation note: opt-in constraints, not BUILTIN_CONSTRAINTS
------------------------------------------------------------------
``MutualExclusionConstraint``/``FunctionalRelationConstraint`` are declared
in cks-core's ``OPTIONAL_CONSTRAINTS`` (see
``cks/constraints/builtin.py``), *not* ``BUILTIN_CONSTRAINTS`` -- a plain
``cks.validate(structure)`` call does not run them. This mirrors how
cks-mcp's own ``detect_contradictions`` tool must explicitly opt in via
``resolve_extensions(["mutual_exclusion", "functional_relation", ...])``
before calling ``ValidateOperation``. This sweeper does the same: it opts
into exactly ``mutual_exclusion``/``functional_relation`` via
``extra_constraints``, the identical "opt in the specific extension, call
cks.validate directly" idiom ``InferenceStalenessSweeper`` already uses for
its own two reasoning-staleness constraints (see that module's docstring).
It deliberately does not opt into ``inference_confidence_conflict`` --
that WARNING-severity belief conflict is already covered end-to-end by
``InferenceStalenessSweeper``/``inference_conflict`` outbox tasks, and this
sweeper only reports the two ERROR-severity, jointly-nonsensical relation
contradictions (see cks-mcp's ``detect_contradictions`` docstring for why
that one is WARNING, not ERROR, and therefore out of scope here).

Design notes
------------
* Runs as an ``asyncio.Task``, same model as the other reasoning sweepers.
  Start it with ``await sweeper.start()`` inside a running event loop, stop
  it with ``await sweeper.stop()``.

* Only active when the storage backend supports the persistent outbox
  (``supports_outbox``) *and* exposes ``list_sessions_modified_since`` --
  same double gate ``ProvenanceStalenessSweeper`` uses. Plain
  ``InMemoryStorage`` supports neither, so the worker task never starts
  for it (rather than starting and silently no-op'ing every sweep).

* A contradiction already escalated for a session on a prior sweep is not
  re-escalated on a later one that still finds it, deduplicated by
  ``(session_id, location)`` -- the diagnostic's relation id, which pins
  down *which* pair of contradictory relations was flagged. Mirrors
  ``InferenceStalenessSweeper``'s own ``_known_diagnostics`` dedup, for the
  same reason: a sweep interval is far shorter than how long a real
  contradiction typically stays unresolved without this.

* The watermark advance/batch-growth discipline (see
  ``_MAX_SWEEP_LIMIT``) is identical to ``InferenceStalenessSweeper``/
  ``ProvenanceStalenessSweeper``: one sweep re-queries with a growing limit
  until it has provably drained everything at or after the old watermark,
  then advances to the wall-clock time the sweep began.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import cks
from cks.constraints.builtin import OPTIONAL_CONSTRAINTS_BY_NAME

from cks_runtime.reasoning.sweeper_status import SweeperStatusMixin

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cks_runtime.session.session import RuntimeSession
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches Provenance/Temporal/GraphFreshness sweepers

# task_type value written to cks_outbox_tasks.
_CONTRADICTION_DETECTED_TASK_TYPE = "contradiction_detected"

# The two ERROR-severity contradiction constraints this sweeper checks for
# -- deliberately not inference_confidence_conflict (WARNING severity,
# already covered by InferenceStalenessSweeper; see module docstring).
_RELEVANT_CONSTRAINT_NAMES = ("mutual_exclusion", "functional_relation")

# Sentinel: attribute name used to duck-type sweep-capable storage.
# Mirrors InferenceStalenessSweeper/ProvenanceStalenessSweeper's own
# _SWEEP_METHODS convention.
_SWEEP_METHODS = ("list_sessions_modified_since",)

# Upper bound on how large a single sweep's re-query can grow while
# draining a backlog -- same safety-valve rationale as
# InferenceStalenessSweeper._MAX_SWEEP_LIMIT.
_MAX_SWEEP_LIMIT = 100_000


def _storage_supports_sweep(storage: object) -> bool:
    return all(callable(getattr(storage, m, None)) for m in _SWEEP_METHODS)


class ContradictionSweeper(SweeperStatusMixin):
    """
    Periodically re-validates recently-modified sessions against the
    ``MutualExclusionConstraint``/``FunctionalRelationConstraint`` extension
    constraints (``mutual_exclusion``/``functional_relation``, opted in via
    ``extra_constraints`` -- see module docstring), and enqueues a
    ``contradiction_detected`` outbox task for each newly-found ERROR
    diagnostic -- to be picked up by cks-mcp's ``critic_agent``, which
    resolves it via ``resolve_contradiction(commit=True)``.

    Mirrors ProvenanceStalenessSweeper's constructor shape and lifecycle
    (start/stop as an asyncio background task, ``run_once``/``sweep_once``
    for tests) so it is wired into ``Runtime.__init__`` the same way.

    Parameters
    ----------
    storage:
        The runtime storage backend. The sweeper is a no-op -- it never
        starts its background task -- when the backend does not support
        the outbox (``supports_outbox`` False) or does not implement
        ``list_sessions_modified_since`` (e.g. ``InMemoryStorage``, which
        supports neither).
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour, same as
        ProvenanceStalenessSweeper/TemporalStalenessSweeper/
        GraphFreshnessSweeper.
    batch_size:
        Initial page size per storage query within a sweep. Grown
        automatically, up to ``_MAX_SWEEP_LIMIT``, mirroring
        InferenceStalenessSweeper/ProvenanceStalenessSweeper's own
        batching.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        batch_size: int = 100,
    ) -> None:
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Sessions modified at or after this instant are candidates for
        # the next sweep. Starts at the Unix epoch so the very first sweep
        # after startup considers every existing session at least once.
        # Same watermark-advance discipline as InferenceStalenessSweeper/
        # ProvenanceStalenessSweeper: only advances once a sweep has
        # provably drained everything at or after the old watermark.
        self._watermark: datetime = datetime.fromtimestamp(0, tz=UTC)

        # session_id -> set of (code, location) pairs already escalated
        # for that session, across all sweeps so far. Without this, a
        # contradiction that hasn't been resolved yet would be
        # re-escalated -- and a new outbox task written -- on every single
        # sweep for as long as it remains unresolved. Mirrors
        # InferenceStalenessSweeper's own `_known_diagnostics` dedup. If
        # the contradiction is later resolved (one of the two conflicting
        # relations removed, e.g. via resolve_contradiction) it drops out
        # of `current` on the next sweep and is cleared from this set, so
        # a subsequent contradiction at the same location is escalated
        # again rather than suppressed forever.
        self._known_diagnostics: dict[str, set[tuple[str, str | None]]] = {}

        self._init_sweeper_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        async with self._control_lock:
            if self._running:
                return
            if not getattr(self._storage, "supports_outbox", False):
                logger.info(
                    "Storage backend does not support outbox; "
                    "ContradictionSweeper will not start."
                )
                return
            if not _storage_supports_sweep(self._storage):
                logger.info(
                    "%s does not support sweep methods; "
                    "ContradictionSweeper will not start.",
                    type(self._storage).__name__,
                )
                return
            self._running = True
            self._task = asyncio.create_task(self._run(), name="cks-contradiction-sweep")
            logger.info(
                "ContradictionSweeper started (interval=%ds, batch=%d).",
                self._interval_seconds,
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
            logger.info("ContradictionSweeper stopped.")

    # ------------------------------------------------------------------
    # Sweep loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            started_at = datetime.now(UTC)
            try:
                result = await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_sweep_error(started_at, exc)
                logger.exception(
                    "ContradictionSweeper sweep failed; will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._interval_seconds)
            desired = self._storage.get_sweeper_desired_running("contradiction")
            # get_sweeper_desired_running may be sync (SQLiteStorage) or
            # async (PostgresStorage/StorageAdapter) -- see the same
            # sync/async duck-typing pattern above for list_fn/enqueue_task.
            if asyncio.iscoroutine(desired):
                desired = await desired
            if desired is False:
                self._running = False
                break

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated
        ``contradiction_detected`` payloads (mainly for tests) --
        payloads for contradictions already known from a prior sweep are
        not repeated here, even though they remain unresolved in the
        outbox.
        """
        sweep_started_at = datetime.now(UTC)
        watermark = self._watermark
        list_fn = self._storage.list_sessions_modified_since

        limit = self._batch_size
        candidates: list[RuntimeSession] = []

        while True:
            result = list_fn(watermark, limit)
            # list_fn may be sync (SQLiteStorage) or async (PostgresStorage).
            batch = await result if asyncio.iscoroutine(result) else result

            if len(batch) < limit:
                candidates = batch
                self._watermark = sweep_started_at
                break

            if limit >= _MAX_SWEEP_LIMIT:
                candidates = batch
                break

            limit *= 4

        supports_outbox = bool(getattr(self._storage, "supports_outbox", False))

        escalated: list[dict[str, Any]] = []
        for session in candidates:
            escalated.extend(
                await self._sweep_session(session, supports_outbox=supports_outbox)
            )

        return escalated

    async def _sweep_session(
        self,
        session: RuntimeSession,
        *,
        supports_outbox: bool,
    ) -> list[dict[str, Any]]:
        structure = session.knowledge_structure
        if structure is None:
            return []

        constraints = [
            OPTIONAL_CONSTRAINTS_BY_NAME[name] for name in _RELEVANT_CONSTRAINT_NAMES
        ]

        result = cks.validate(structure, extra_constraints=constraints)
        relevant_diagnostics = [
            d
            for d in result.diagnostics
            if d.identity in {c.identity for c in constraints}
            and d.severity.value == "error"
        ]

        current = {(d.identity, d.location) for d in relevant_diagnostics}
        known = self._known_diagnostics.get(session.session_id, set())
        new_pairs = current - known

        new_payloads: list[dict[str, Any]] = []
        if new_pairs:
            new_diagnostics = [
                d for d in relevant_diagnostics if (d.identity, d.location) in new_pairs
            ]
            for diagnostic in new_diagnostics:
                payload = {
                    "code": diagnostic.identity,
                    "severity": diagnostic.severity.value,
                    "message": diagnostic.message,
                    "location": diagnostic.location,
                }
                new_payloads.append(payload)

                if supports_outbox:
                    enqueue_result = self._storage.enqueue_task(
                        task_type=_CONTRADICTION_DETECTED_TASK_TYPE,
                        session_id=session.session_id,
                        payload=json.dumps(payload),
                    )
                    # enqueue_task may be sync (SQLiteStorage) or async
                    # (PostgresStorage).
                    if asyncio.iscoroutine(enqueue_result):
                        await enqueue_result

        if current:
            self._known_diagnostics[session.session_id] = current
        else:
            self._known_diagnostics.pop(session.session_id, None)

        return new_payloads

    # ------------------------------------------------------------------
    # Convenience: run a single sweep synchronously (useful in tests)
    # ------------------------------------------------------------------

    async def run_once(self) -> list[dict[str, Any]]:
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
            result = await self.sweep_once()
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
            agent_id="contradiction",
            running=self._running,
            interval_seconds=self._interval_seconds,
        )
