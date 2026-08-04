"""
TemporalStalenessSweeper: background detection of expired
time-bounded facts (ADR-011).

Structural counterpart to InferenceStalenessSweeper (ADR-009) and, in
particular, ProvenanceStalenessSweeper (ADR-010), whose lifecycle and
outbox-escalation shape this module follows directly. Where
ProvenanceStalenessSweeper walks VerificationRecords looking for ones
whose `checked_at` has exceeded a TTL, this sweeper walks every object
in a session looking for ones whose `valid_until` has already passed
-- the same question cks-core's opt-in `TemporalValidityConstraint`
(ADR-003) answers, but proactively rather than only on an explicit
`validate_knowledge` call.

This sweeper is detection-only. It does not decide what an expired
fact should become (archived, superseded, extended) -- that stays
with a future Critic Agent via cks-mcp's `resolve_temporal_conflict`
tool, consistent with Runtime never originating decisions, only
orchestrating (see ADR-001, Runtime Layering; ADR-010 makes the same
choice for provenance re-verification).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import cks
from cks.constraints.temporal import TemporalValidityConstraint
from cks.diagnostics import DiagnosticSeverity

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cks_runtime.session.session import RuntimeSession
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches ProvenanceStalenessSweeper
DEFAULT_BATCH_SIZE = 100

# task_type value written to cks_outbox_tasks -- see ADR-011.
_TEMPORAL_CONFLICT_TASK_TYPE = "temporal_conflict"

# The diagnostic code TemporalValidityConstraint reports expired
# objects under (see cks-core ADR-003 / cks.constraints.temporal).
_TEMPORAL_VALIDITY_CODE = "CKS-EXT-TEMPORAL-VALIDITY"

# Sentinel: attribute name used to duck-type sweep-capable storage.
# Mirrors ProvenanceStalenessSweeper's own _SWEEP_METHODS convention.
_SWEEP_METHODS = ("list_sessions_modified_since",)

# Upper bound on how large a single sweep's re-query can grow while
# draining a backlog -- same safety-valve rationale as
# ProvenanceStalenessSweeper._MAX_SWEEP_LIMIT.
_MAX_SWEEP_LIMIT = 100_000


def _storage_supports_sweep(storage: object) -> bool:
    return all(callable(getattr(storage, m, None)) for m in _SWEEP_METHODS)


class TemporalStalenessSweeper:
    """
    Periodically scans every session's objects for ones whose
    `valid_until` has passed (via cks-core's opt-in
    `TemporalValidityConstraint`, ADR-003), and enqueues a
    `temporal_conflict` outbox task for each newly-expired one found --
    to be picked up by cks-mcp's critic_agent, via a future
    `resolve_temporal_conflict` tool (ADR-011).

    Mirrors ProvenanceStalenessSweeper's constructor shape and
    lifecycle (start/stop as an asyncio background task, `sweep_once`
    for tests) so it is wired into `Runtime.__init__` the same way.

    Parameters
    ----------
    storage:
        The runtime storage backend. The sweeper is a no-op when the
        backend does not implement `list_sessions_modified_since` or
        `supports_outbox` (e.g. `InMemoryStorage`) -- same convention
        InferenceStalenessSweeper and ProvenanceStalenessSweeper
        already follow.
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour, same as
        ProvenanceStalenessSweeper.
    batch_size:
        Initial page size per storage query within a sweep. Grown
        automatically, up to `_MAX_SWEEP_LIMIT`, mirroring
        ProvenanceStalenessSweeper's own batching.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._constraint = TemporalValidityConstraint()

        # Sessions modified at or after this instant are candidates for
        # the next sweep. Starts at the Unix epoch so the very first
        # sweep after startup considers every existing session at
        # least once. Same watermark-advance discipline as
        # ProvenanceStalenessSweeper: only advances once a sweep has
        # provably drained everything at or after the old watermark.
        self._watermark: datetime = datetime.fromtimestamp(0, tz=UTC)

        # session_id -> set of object locations already escalated as
        # expired for that session. A sweep interval (default hourly)
        # is far shorter than how long a real conflict typically stays
        # unresolved, so without this a fact would be re-escalated --
        # and a new outbox task written -- on every single sweep for
        # as long as it remains expired. Mirrors
        # ProvenanceStalenessSweeper's own `_known_stale` dedup. If the
        # object's `valid_until` is later moved forward (or the object
        # removed) it drops out of `current` on the next sweep and is
        # cleared from this set, so a subsequent expiry is escalated
        # again rather than suppressed forever.
        self._known_stale: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        if not getattr(self._storage, "supports_outbox", False):
            logger.info(
                "Storage backend does not support outbox; "
                "TemporalStalenessSweeper will not start."
            )
            return
        if not _storage_supports_sweep(self._storage):
            logger.info(
                "%s does not support sweep methods; "
                "TemporalStalenessSweeper will not start.",
                type(self._storage).__name__,
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="cks-temporal-sweep")
        logger.info(
            "TemporalStalenessSweeper started (interval=%ds, batch=%d).",
            self._interval_seconds,
            self._batch_size,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TemporalStalenessSweeper stopped.")

    # ------------------------------------------------------------------
    # Sweep loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "TemporalStalenessSweeper sweep failed; will retry next interval."
                )
            await asyncio.sleep(self._interval_seconds)

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated conflict
        payloads (mainly for tests) -- payloads for objects that were
        already known-stale from a prior sweep are not repeated here,
        even though they remain unresolved in the outbox.
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

        escalated: list[dict[str, Any]] = []
        supports_outbox = bool(getattr(self._storage, "supports_outbox", False))

        for session in candidates:
            escalated.extend(
                self._sweep_session(session, supports_outbox=supports_outbox)
            )

        return escalated

    def _sweep_session(
        self,
        session: RuntimeSession,
        *,
        supports_outbox: bool,
    ) -> list[dict[str, Any]]:
        structure = session.knowledge_structure
        if structure is None:
            return []

        result = cks.validate(structure, extra_constraints=[self._constraint])
        expired_diagnostics = [
            d
            for d in result.diagnostics
            if d.identity == _TEMPORAL_VALIDITY_CODE
            and d.severity == DiagnosticSeverity.WARNING
        ]

        objects_by_id = {obj.identity.id: obj for obj in structure.objects}

        current_stale: set[str] = set()
        new_payloads: list[dict[str, Any]] = []

        for diagnostic in expired_diagnostics:
            location = diagnostic.location
            if location is None:
                continue

            current_stale.add(location)

            if location in self._known_stale.get(session.session_id, set()):
                continue  # already escalated on a prior sweep

            obj = objects_by_id.get(location)
            valid_until = obj.structure.get("valid_until") if obj is not None else None

            payload = {
                "object_id": location,
                "object_type": obj.identity.type if obj is not None else None,
                "valid_until": valid_until,
                "message": diagnostic.message,
                "reason": "valid_until_expired",
            }
            new_payloads.append(payload)

            if supports_outbox:
                self._storage.enqueue_task(
                    task_type=_TEMPORAL_CONFLICT_TASK_TYPE,
                    session_id=session.session_id,
                    payload=json.dumps(payload),
                )

        if current_stale:
            self._known_stale[session.session_id] = current_stale
        else:
            self._known_stale.pop(session.session_id, None)

        return new_payloads

    # ------------------------------------------------------------------
    # Convenience: run a single sweep synchronously (useful in tests)
    # ------------------------------------------------------------------

    async def run_once(self) -> list[dict[str, Any]]:
        """Trigger one sweep immediately, without starting the background loop."""
        return await self.sweep_once()
