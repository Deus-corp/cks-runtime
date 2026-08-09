"""
ProvenanceStalenessSweeper: background detection of expired
VerificationRecords.

Structural counterpart to InferenceStalenessSweeper (ADR-009). Where that
sweeper walks active InferenceSteps looking for stale premises, this one
walks VerificationRecords looking for ones whose `checked_at` timestamp
has exceeded a TTL, and escalates a ProvenanceStalenessConflict onto the
same outbox `claim_conflict_task` machinery already used for
`gossip_conflict` / `inference_conflict` tasks (see ADR-010).

This sweeper is detection-only. It does not perform the outbound HTTP
re-check itself -- that stays in cks-mcp's `verify_source`/
`refresh_verification`, consistent with Runtime never originating external
I/O or holding signing material (see ADR-001, Runtime Layering).

Naming note (ADR-010 vs. cks-core reality)
-------------------------------------------
ADR-010's prose refers to the timestamp field as ``verified_at``. The
actual field cks-core's ``VerificationRecordIntegrityConstraint`` (and
cks-mcp's ``verify_source``, which is the sole constructor of these
objects) reads and writes is ``checked_at`` -- there is no ``verified_at``
key anywhere in a ``VerificationRecord``'s structure. This module follows
the real field name, ``checked_at``; the ADR's prose is describing the
same field informally, not a different one, so it is not worth amending
for this alone.

Likewise, ``VerificationRecord`` objects do not carry a ``source_url``
field themselves -- the URL that was checked lives on the *subject*
object (typically a ``Document``, see ``ingest_document``), linked via
the record's single ``verified_by`` relation. This sweeper resolves that
relation and includes the subject's ``url`` in the escalated payload when
the subject happens to carry one, and omits it (rather than guessing)
when it doesn't -- e.g. a `manual_review` record over a non-Document
subject.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cks_runtime.reasoning.sweeper_status import SweeperStatusMixin

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cks_runtime.session.session import RuntimeSession
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_PROVENANCE_TTL_SECONDS = 30 * 24 * 3600  # 30 days, see ADR-010
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches InferenceStalenessSweeper

# task_type value written to cks_outbox_tasks -- see ADR-010.
_PROVENANCE_CONFLICT_TASK_TYPE = "provenance_conflict"

_VERIFICATION_RECORD_TYPE = "VerificationRecord"
_VERIFIED_BY_RELATION = "verified_by"
_CHECKED_AT_KEY = "checked_at"

# Sentinel: attribute name used to duck-type sweep-capable storage.
# Mirrors InferenceStalenessSweeper's own _SWEEP_METHODS convention.
_SWEEP_METHODS = ("list_sessions_modified_since",)

# Upper bound on how large a single sweep's re-query can grow while
# draining a backlog -- same safety-valve rationale as
# InferenceStalenessSweeper._MAX_SWEEP_LIMIT.
_MAX_SWEEP_LIMIT = 100_000


def _storage_supports_sweep(storage: object) -> bool:
    return all(callable(getattr(storage, m, None)) for m in _SWEEP_METHODS)


def _parse_checked_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None


class ProvenanceStalenessSweeper(SweeperStatusMixin):
    """
    Periodically scans VerificationRecord objects across sessions for ones
    whose `checked_at` has exceeded `ttl_seconds`, and enqueues a
    `provenance_conflict` outbox task for each newly-stale one found -- to
    be picked up by cks-mcp's critic_agent, which resolves it via
    `refresh_verification(auto_resolve=True, commit=True)`.

    Mirrors InferenceStalenessSweeper's constructor shape and lifecycle
    (start/stop as an asyncio background task, `run_once` for tests) so it
    is wired into `Runtime.__init__` the same way.

    Parameters
    ----------
    storage:
        The runtime storage backend. The sweeper is a no-op when the
        backend does not implement `list_sessions_modified_since` (e.g.
        `InMemoryStorage`) -- same convention InferenceStalenessSweeper
        and GarbageCollector already follow.
    ttl_seconds:
        How old a `checked_at` may get before its record is considered
        stale. Defaults to 30 days (see ADR-010).
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour.
    batch_size:
        Initial page size per storage query within a sweep. Grown
        automatically, up to `_MAX_SWEEP_LIMIT`, mirroring
        InferenceStalenessSweeper's own batching.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        ttl_seconds: int = DEFAULT_PROVENANCE_TTL_SECONDS,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        batch_size: int = 100,
    ) -> None:
        self._storage = storage
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Sessions modified at or after this instant are candidates for
        # the next sweep. Starts at the Unix epoch so the very first
        # sweep after startup considers every existing session at least
        # once. Same watermark-advance discipline as
        # InferenceStalenessSweeper: only advances once a sweep has
        # provably drained everything at or after the old watermark.
        self._watermark: datetime = datetime.fromtimestamp(0, tz=UTC)

        # session_id -> set of record_ids already escalated as stale for
        # that session. A sweep interval (default hourly) is far shorter
        # than how long a real conflict typically stays unresolved, so
        # without this a record would be re-escalated -- and a new
        # outbox task written -- on every single sweep for as long as it
        # stays unresolved. Mirrors InferenceStalenessSweeper's own
        # `_known_diagnostics` dedup. If a record is later re-verified
        # (`checked_at` moves forward, e.g. cks-mcp's
        # `refresh_verification`) it drops out of `current` on the next
        # sweep and is cleared from this set, so a subsequent expiry is
        # escalated again rather than suppressed forever.
        self._known_stale: dict[str, set[str]] = {}

        self._init_sweeper_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        if not getattr(self._storage, 'supports_outbox', False):
            logger.info(
                "Storage backend does not support outbox; "
                "ProvenanceStalenessSweeper will not start."
            )
            return
        if not _storage_supports_sweep(self._storage):
            logger.info(
                "%s does not support sweep methods; "
                "ProvenanceStalenessSweeper will not start.",
                type(self._storage).__name__,
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="cks-provenance-sweep")
        logger.info(
            "ProvenanceStalenessSweeper started "
            "(ttl=%ds, interval=%ds, batch=%d).",
            self._ttl_seconds,
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
        logger.info("ProvenanceStalenessSweeper stopped.")

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
                    "ProvenanceStalenessSweeper sweep failed; "
                    "will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._interval_seconds)

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated conflict
        payloads (mainly for tests) -- payloads for records that were
        already known-stale from a prior sweep are not repeated here,
        even though they remain unresolved in the outbox.
        """
        sweep_started_at = datetime.now(UTC)
        cutoff = sweep_started_at - timedelta(seconds=self._ttl_seconds)
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
                await self._sweep_session(session, cutoff, supports_outbox=supports_outbox)
            )

        return escalated

    async def _sweep_session(
        self,
        session: RuntimeSession,
        cutoff: datetime,
        *,
        supports_outbox: bool,
    ) -> list[dict[str, Any]]:
        structure = session.knowledge_structure
        if structure is None:
            return []

        objects = structure.objects
        objects_by_id = {obj.identity.id: obj for obj in objects}

        # record_id -> subject_id, from each record's single verified_by
        # relation. Same participant-unpacking shape
        # VerificationRecordIntegrityConstraint uses in cks-core.
        subject_by_record: dict[str, str] = {}
        for relation in structure.relations():
            if relation.relation_type != _VERIFIED_BY_RELATION:
                continue
            if len(relation.participants) != 2:
                continue
            subject_id, record_id = relation.participants
            subject_by_record[record_id] = subject_id

        current_stale: set[str] = set()
        new_payloads: list[dict[str, Any]] = []

        for obj in objects:
            if obj.identity.type != _VERIFICATION_RECORD_TYPE:
                continue

            checked_at_raw = obj.structure.get(_CHECKED_AT_KEY)
            checked_at = _parse_checked_at(checked_at_raw)
            if checked_at is None:
                # Malformed/missing checked_at is a structural-validity
                # problem VerificationRecordIntegrityConstraint already
                # catches on the ordinary validate path -- not this
                # sweeper's concern, and not something it can safely
                # treat as "stale" or "fresh".
                continue
            if checked_at >= cutoff:
                continue

            record_id = obj.identity.id
            current_stale.add(record_id)

            if record_id in self._known_stale.get(session.session_id, set()):
                continue  # already escalated on a prior sweep

            subject_id = subject_by_record.get(record_id)
            subject_obj = objects_by_id.get(subject_id) if subject_id else None
            source_url = None
            if subject_obj is not None:
                source_url = subject_obj.structure.get("url")

            payload = {
                "record_id": record_id,
                "subject_id": subject_id,
                "source_url": source_url,
                "checked_at": checked_at_raw,
                "reason": "ttl_expired",
            }
            new_payloads.append(payload)

            if supports_outbox:
                self._storage.enqueue_task(
                    task_type=_PROVENANCE_CONFLICT_TASK_TYPE,
                    session_id=session.session_id,
                    payload=json.dumps(payload),
                )

        if current_stale:
            self._known_stale[session.session_id] = current_stale
        else:
            self._known_stale.pop(session.session_id, None)

        return new_payloads

    # ------------------------------------------------------------------
    # Status (agent_status / list_agents, see cks-mcp)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self.sweeper_status(
            agent_id="provenance_staleness",
            running=self._running,
            interval_seconds=self._interval_seconds,
        )