"""
GraphFreshnessSweeper: background detection of stale registered graphs
(Memory Agent v2).

Structural counterpart to ProvenanceStalenessSweeper. Where that
sweeper walks VerificationRecords inside session content looking for
an expired `checked_at`, this one walks the `graph_registry` table
itself (Memory Agent v1, see `register_graph`/`get_graph`/`list_graphs`)
looking for entries whose `updated_at` has exceeded a TTL, and
escalates a `graph_outdated` outbox task for each newly-stale one
found -- to be picked up by a future update agent via cks-mcp.

This sweeper is detection-only. It does not refresh the graph itself
(no HTTP requests to repositories, no re-construction of the
underlying session) -- that stays with a future cks-mcp agent,
consistent with Runtime never originating decisions or external I/O,
only orchestrating (see ADR-001, Runtime Layering; ADR-010 makes the
same choice for provenance re-verification).
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
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_GRAPH_FRESHNESS_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches ProvenanceStalenessSweeper

# task_type value written to cks_outbox_tasks.
_GRAPH_OUTDATED_TASK_TYPE = "graph_outdated"


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None


class GraphFreshnessSweeper(SweeperStatusMixin):
    """
    Periodically scans every entry in `graph_registry` for ones whose
    `updated_at` has exceeded `ttl_seconds`, and enqueues a
    `graph_outdated` outbox task for each newly-stale one found -- to
    be picked up by a future cks-mcp update agent.

    Mirrors ProvenanceStalenessSweeper's constructor shape and
    lifecycle (start/stop as an asyncio background task, `run_once`/
    `sweep_once` for tests).

    Parameters
    ----------
    storage:
        The runtime storage backend. The sweeper is a no-op when the
        backend does not support the outbox (`supports_outbox`
        False) -- e.g. `InMemoryStorage`, which implements the graph
        registry but not the outbox. This sweeper does not require
        `list_sessions_modified_since`/duck-typed sweep methods the
        way the session-content sweepers do -- it only needs
        `list_graphs`, which every `RuntimeStorage`/
        `AsyncRuntimeStorage` implements (as a no-op returning `[]`
        by default).
    ttl_seconds:
        How old a graph's `updated_at` may get before it's considered
        outdated. Defaults to 7 days.
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour, same as
        ProvenanceStalenessSweeper/TemporalStalenessSweeper.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        ttl_seconds: int = DEFAULT_GRAPH_FRESHNESS_TTL_SECONDS,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._storage = storage
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # graph name -> whether it was already escalated as outdated on
        # a prior sweep. A sweep interval (default hourly) is far
        # shorter than how long a real staleness typically stays
        # unresolved, so without this a graph would be re-escalated --
        # and a new outbox task written -- on every single sweep for as
        # long as it remains outdated. Mirrors ProvenanceStalenessSweeper's
        # own `_known_stale` dedup. If the graph is later refreshed
        # (`updated_at` moves forward, e.g. a future update agent
        # re-registering it) it drops out of `current` on the next
        # sweep and is cleared from this set, so a subsequent staleness
        # is escalated again rather than suppressed forever.
        self._known_stale: set[str] = set()

        self._init_sweeper_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        if not getattr(self._storage, "supports_outbox", False):
            logger.info(
                "Storage backend does not support outbox; "
                "GraphFreshnessSweeper will not start."
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="cks-graph-freshness-sweep")
        logger.info(
            "GraphFreshnessSweeper started (ttl=%ds, interval=%ds).",
            self._ttl_seconds,
            self._interval_seconds,
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
        logger.info("GraphFreshnessSweeper stopped.")

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
                    "GraphFreshnessSweeper sweep failed; will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._interval_seconds)

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated
        `graph_outdated` payloads (mainly for tests) -- payloads for
        graphs that were already known-stale from a prior sweep are
        not repeated here, even though they remain unresolved in the
        outbox.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ttl_seconds)

        list_fn = self._storage.list_graphs
        result = list_fn()
        # list_graphs may be sync (SQLiteStorage/InMemoryStorage) or
        # async (PostgresStorage).
        graphs = await result if asyncio.iscoroutine(result) else result

        supports_outbox = bool(getattr(self._storage, "supports_outbox", False))

        current_stale: set[str] = set()
        new_payloads: list[dict[str, Any]] = []

        for graph in graphs:
            name = graph.get("name")
            if not name:
                continue

            updated_at_raw = graph.get("updated_at")
            updated_at = _parse_timestamp(updated_at_raw)
            if updated_at is None:
                # Malformed/missing updated_at isn't this sweeper's
                # concern -- it can't safely decide staleness for it.
                continue
            if updated_at >= cutoff:
                continue

            current_stale.add(name)

            if name in self._known_stale:
                continue  # already escalated on a prior sweep

            payload = {
                "name": name,
                "session_id": graph.get("session_id"),
                "updated_at": updated_at_raw,
                "ttl_seconds": self._ttl_seconds,
                "reason": "ttl_expired",
            }
            new_payloads.append(payload)

            if supports_outbox:
                session_id = graph.get("session_id") or ""
                enqueue_result = self._storage.enqueue_task(
                    task_type=_GRAPH_OUTDATED_TASK_TYPE,
                    session_id=session_id,
                    payload=json.dumps(payload),
                )
                # enqueue_task may be sync (SQLiteStorage) or async
                # (PostgresStorage).
                if asyncio.iscoroutine(enqueue_result):
                    await enqueue_result

        self._known_stale = current_stale

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
            agent_id="graph_freshness",
            running=self._running,
            interval_seconds=self._interval_seconds,
        )
