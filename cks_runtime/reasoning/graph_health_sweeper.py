"""
GraphHealthSweeper: background computation of an aggregate 0.0-1.0
health score for every registered graph (``graph_registry``, Memory
Agent v1), combining the same signals cks-mcp's ``check_graph_health``
tool reports on demand -- version freshness, TTL freshness,
contradictions, verification coverage, dead-lettered conflict tasks --
into one number, and escalating a ``health_check`` outbox task for any
graph whose score drops below a configurable threshold.

Why this duplicates logic instead of calling cks-mcp's tool
-------------------------------------------------------------
Runtime never originates decisions or holds an MCP-server-to-itself
calling convention -- it only orchestrates (see ADR-001, Runtime
Layering). ``GraphAutoUpdateSweeper``'s module docstring already
covers why this sweeper doesn't become an HTTP client of the MCP
server purely to re-enter cks-mcp code that already runs in-process
there: that wiring is left for a follow-up once there's a real
calling convention to hang it off of. Until then, this sweeper reuses
the *runtime-side* building blocks the equivalent cks-mcp checks are
themselves built on:

- version freshness: the same GitHub raw-file cross-check
  ``GraphAutoUpdateSweeper``/cks-mcp's ``check_component_versions``
  perform, via that module's ``_resolve_component``/
  ``_fetch_version_sync``/``_is_outdated`` helpers (same package,
  imported directly rather than duplicated a third time).
- TTL freshness: the same ``updated_at`` vs. TTL comparison
  ``GraphFreshnessSweeper``/cks-mcp's ``check_graph_freshness``
  perform.
- contradictions: the same ``mutual_exclusion``/``functional_relation``
  constraint check ``ContradictionSweeper``/cks-mcp's
  ``detect_contradictions`` perform, via ``cks.validate``.
- verification coverage: a direct scan of ``VerificationRecord``
  objects' ``checked_at``, the same field
  ``ProvenanceStalenessSweeper`` already walks.
- dead-lettered tasks: ``storage.list_dead_letter_tasks``, filtered to
  this graph's ``session_id`` client-side (the outbox has no
  session_id-indexed dead-letter query -- see cks-mcp's
  ``check_graph_health`` handler, which does the same client-side
  filter).

This sweeper is detection-only, like every other reasoning sweeper in
this package: it computes and escalates, and never applies any fix
itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import cks
from cks.constraints.builtin import OPTIONAL_CONSTRAINTS_BY_NAME

from cks_runtime.reasoning.graph_auto_update_sweeper import (
    _COMPONENT_TYPE,
    _fetch_version_sync,
    _is_outdated,
    _resolve_component,
)
from cks_runtime.reasoning.sweeper_status import SweeperStatusMixin

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cks_runtime.session.session import RuntimeSession
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches the other graph sweepers
DEFAULT_MIN_SCORE = 0.7

# task_type value written to cks_outbox_tasks.
_HEALTH_CHECK_TASK_TYPE = "health_check"

_VERIFICATION_RECORD_TYPE = "VerificationRecord"
_CHECKED_AT_KEY = "checked_at"
_VERIFICATION_COVERAGE_TTL_SECONDS = 30 * 24 * 3600  # 30 days

# Same two ERROR-severity contradiction/conflict constraints
# ContradictionSweeper checks for -- see that module's docstring for
# why inference_confidence_conflict (WARNING severity) is excluded.
_CONTRADICTION_CONSTRAINT_NAMES = ("mutual_exclusion", "functional_relation")

# Weights used to combine the individual metric scores into the
# overall health_score. Mirrors cks-mcp's check_graph_health exactly,
# so the two report the same number for the same graph.
_WEIGHT_VERSION_FRESHNESS = 0.3
_WEIGHT_TTL_FRESHNESS = 0.1
_WEIGHT_CONTRADICTIONS = 0.3
_WEIGHT_VERIFICATION_COVERAGE = 0.2
_WEIGHT_DEAD_LETTER = 0.1


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def _version_freshness_score(session: RuntimeSession) -> float:
    structure = session.knowledge_structure
    objects = getattr(structure, "objects", None) or []
    components = [
        obj
        for obj in objects
        if getattr(getattr(obj, "identity", None), "type", None) == _COMPONENT_TYPE
        and "version" in getattr(obj, "structure", {})
    ]
    if not components:
        return 1.0

    up_to_date = 0
    checked = 0
    for obj in components:
        component_name = obj.identity.name or obj.identity.id
        graph_version = obj.structure.get("version")

        repo, candidate_paths = _resolve_component(component_name, obj.structure)
        if repo is None:
            continue

        actual_version, _error = await asyncio.to_thread(
            _fetch_version_sync, repo, candidate_paths
        )
        if actual_version is None:
            continue

        checked += 1
        if not _is_outdated(graph_version, actual_version):
            up_to_date += 1

    # A component that couldn't be resolved/fetched is excluded from
    # the ratio entirely (unknown, not "outdated") -- if none could be
    # checked at all, treat it the same as "nothing to check".
    return 1.0 if checked == 0 else up_to_date / checked


def _ttl_freshness_score(graph: dict[str, Any], ttl_seconds: int) -> float:
    updated_at = _parse_timestamp(graph.get("updated_at"))
    if updated_at is None:
        return 0.0
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
    return 1.0 if updated_at >= cutoff else 0.0


def _contradictions_score(session: RuntimeSession) -> float:
    structure = session.knowledge_structure
    if structure is None:
        return 1.0
    constraints = [
        OPTIONAL_CONSTRAINTS_BY_NAME[name] for name in _CONTRADICTION_CONSTRAINT_NAMES
    ]
    result = cks.validate(structure, extra_constraints=constraints)
    constraint_ids = {c.identity for c in constraints}
    count = sum(
        1
        for d in result.diagnostics
        if d.identity in constraint_ids and d.severity.value == "error"
    )
    return 1.0 if count == 0 else 0.0


def _verification_coverage_score(session: RuntimeSession) -> float:
    structure = session.knowledge_structure
    objects = getattr(structure, "objects", None) or []
    records = [
        obj
        for obj in objects
        if getattr(getattr(obj, "identity", None), "type", None)
        == _VERIFICATION_RECORD_TYPE
    ]
    if not records:
        return 1.0

    cutoff = datetime.now(UTC) - timedelta(seconds=_VERIFICATION_COVERAGE_TTL_SECONDS)
    fresh = sum(
        1
        for record in records
        if (checked_at := _parse_timestamp(record.structure.get(_CHECKED_AT_KEY)))
        is not None
        and checked_at >= cutoff
    )
    return fresh / len(records)


async def _dead_letter_score(
    storage: RuntimeStorage | AsyncRuntimeStorage, session_id: str
) -> float:
    result = storage.list_dead_letter_tasks()
    tasks = await result if asyncio.iscoroutine(result) else result
    count = sum(1 for task in tasks if task.session_id == session_id)
    return 1.0 if count == 0 else 0.5


class GraphHealthSweeper(SweeperStatusMixin):
    """
    Periodically scans every entry in ``graph_registry``, computes an
    aggregate health score for each (see module docstring for the
    signals combined and their weights), and enqueues a
    ``health_check`` outbox task for each graph whose score drops
    below ``min_score`` -- to be picked up by an operator or a future
    cks-mcp consumer.

    Mirrors GraphFreshnessSweeper's constructor shape, lifecycle
    (start/stop as an asyncio background task, ``run_once``/
    ``sweep_once`` for tests), and dedup strategy (``_known_unhealthy``).

    Parameters
    ----------
    storage:
        The runtime storage backend. No-op when it doesn't support
        the outbox (``supports_outbox`` False), e.g. ``InMemoryStorage``.
    min_score:
        Health score threshold below which a graph is escalated.
        Defaults to 0.7.
    ttl_seconds:
        TTL used for the version-freshness component of the score.
        Defaults to 7 days, same as ``GraphFreshnessSweeper``'s
        default.
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
        ttl_seconds: int = 7 * 24 * 3600,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._storage = storage
        self._min_score = min_score
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # graph name -> already escalated as unhealthy on a prior
        # sweep. Same rationale as GraphFreshnessSweeper._known_stale:
        # without this, a graph below the threshold would get a fresh
        # outbox task on every single sweep for as long as it stays
        # unhealthy. Cleared once the graph's score recovers above
        # min_score, so a later regression is escalated again rather
        # than suppressed forever.
        self._known_unhealthy: set[str] = set()

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
                    "GraphHealthSweeper will not start."
                )
                return
            self._running = True
            self._task = asyncio.create_task(self._run(), name="cks-graph-health-sweep")
            logger.info(
                "GraphHealthSweeper started (min_score=%.2f, interval=%ds).",
                self._min_score,
                self._interval_seconds,
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
            logger.info("GraphHealthSweeper stopped.")

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
                    "GraphHealthSweeper sweep failed; will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._interval_seconds)
            desired = self._storage.get_sweeper_desired_running("graph_health")
            # get_sweeper_desired_running may be sync (SQLiteStorage) or
            # async (PostgresStorage/StorageAdapter).
            if asyncio.iscoroutine(desired):
                desired = await desired
            if desired is False:
                self._running = False
                break

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated
        ``health_check`` payloads (mainly for tests) -- payloads for
        graphs that were already known-unhealthy from a prior sweep
        are not repeated here, even though they remain unresolved in
        the outbox.
        """
        list_fn = self._storage.list_graphs
        result = list_fn()
        graphs = await result if asyncio.iscoroutine(result) else result

        supports_outbox = bool(getattr(self._storage, "supports_outbox", False))

        current_unhealthy: set[str] = set()
        new_payloads: list[dict[str, Any]] = []

        for graph in graphs:
            name = graph.get("name")
            session_id = graph.get("session_id")
            if not name or not session_id:
                continue

            score, metrics = await self._score_graph(graph, session_id)
            if score is None:
                continue  # session not available -- can't score it

            if score >= self._min_score:
                continue

            current_unhealthy.add(name)

            if name in self._known_unhealthy:
                continue  # already escalated on a prior sweep

            payload = {
                "name": name,
                "session_id": session_id,
                "health_score": score,
                "metrics": metrics,
                "min_score": self._min_score,
            }
            new_payloads.append(payload)

            if supports_outbox:
                enqueue_result = self._storage.enqueue_task(
                    task_type=_HEALTH_CHECK_TASK_TYPE,
                    session_id=session_id,
                    payload=json.dumps(payload),
                )
                if asyncio.iscoroutine(enqueue_result):
                    await enqueue_result

        self._known_unhealthy = current_unhealthy

        return new_payloads

    async def _score_graph(
        self, graph: dict[str, Any], session_id: str
    ) -> tuple[float | None, dict[str, float]]:
        load_fn = self._storage.load_session
        result = load_fn(session_id)
        session = await result if asyncio.iscoroutine(result) else result
        if session is None:
            logger.warning(
                "GraphHealthSweeper: session '%s' for graph '%s' is not "
                "available; skipping.",
                session_id,
                graph.get("name"),
            )
            return None, {}

        version_freshness = await _version_freshness_score(session)
        ttl_freshness = _ttl_freshness_score(graph, self._ttl_seconds)
        contradictions = _contradictions_score(session)
        verification_coverage = _verification_coverage_score(session)
        dead_letter = await _dead_letter_score(self._storage, session_id)

        metrics = {
            "version_freshness": version_freshness,
            "ttl_freshness": ttl_freshness,
            "contradictions": contradictions,
            "verification_coverage": verification_coverage,
            "dead_letter": dead_letter,
        }

        score = (
            _WEIGHT_VERSION_FRESHNESS * version_freshness
            + _WEIGHT_TTL_FRESHNESS * ttl_freshness
            + _WEIGHT_CONTRADICTIONS * contradictions
            + _WEIGHT_VERIFICATION_COVERAGE * verification_coverage
            + _WEIGHT_DEAD_LETTER * dead_letter
        )
        return score, metrics

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
            agent_id="graph_health",
            running=self._running,
            interval_seconds=self._interval_seconds,
        )
