"""
ProvenanceStalenessSweeper: background detection of expired
VerificationRecords.

Structural counterpart to InferenceStalenessSweeper (ADR-009). Where that
sweeper walks active InferenceSteps looking for stale premises, this one
walks VerificationRecords looking for ones whose `verified_at` timestamp
has exceeded a TTL, and escalates a ProvenanceStalenessConflict onto the
same outbox `claim_conflict_task` machinery already used for
`gossip_conflict` / `inference_conflict` tasks (see ADR-010).

This sweeper is detection-only. It does not perform the outbound HTTP
re-check itself -- that stays in cks-mcp's `verify_source`/
`refresh_verification`, consistent with Runtime never originating external
I/O or holding signing material (see ADR-001, Runtime Layering).

STATUS: skeleton / not wired into Runtime.__init__ yet. See ADR-010 for the
design this implements.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from cks_runtime.storage.storage import RuntimeStorage

logger = logging.getLogger(__name__)

DEFAULT_PROVENANCE_TTL_SECONDS = 30 * 24 * 3600  # 30 days, see ADR-010
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches InferenceStalenessSweeper


class ProvenanceStalenessSweeper:
    """
    Periodically scans VerificationRecord objects across sessions for ones
    whose `verified_at` has exceeded `ttl_seconds`, and enqueues a
    `provenance_conflict` outbox task for each one found -- to be picked
    up by cks-mcp's critic_agent, which resolves it via
    `refresh_verification(auto_resolve=True, commit=True)`.

    Mirrors InferenceStalenessSweeper's constructor shape and lifecycle
    (start/stop as an asyncio background task) so it can be wired into
    `Runtime.__init__` the same way.
    """

    def __init__(
        self,
        storage: RuntimeStorage,
        *,
        ttl_seconds: int = DEFAULT_PROVENANCE_TTL_SECONDS,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._storage = storage
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("provenance staleness sweep failed")
            await asyncio.sleep(self._interval_seconds)

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of escalated conflict payloads
        (mainly for tests). Real implementation TODO:

        1. Query storage for VerificationRecord objects across sessions
           (needs a new RuntimeStorage query method, e.g.
           `list_verification_records(older_than: datetime)` --
           analogous to however InferenceStalenessSweeper enumerates
           active InferenceSteps; check that implementation for the
           established query pattern before adding a new one).
        2. For each record older than `ttl_seconds`, build a
           ProvenanceStalenessConflict payload:
           {"record_id": ..., "source_url": ..., "verified_at": ...,
            "reason": "ttl_expired"}.
        3. Enqueue via the same outbox-write path
           InferenceStalenessSweeper / GossipConflictDetected use,
           with task_type="provenance_conflict".
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ttl_seconds)
        raise NotImplementedError(
            "sweep_once: needs RuntimeStorage.list_verification_records "
            "(or equivalent) -- see ADR-010 for the design; not yet "
            f"implemented. cutoff would be {cutoff.isoformat()}"
        )