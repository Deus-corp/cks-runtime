"""
SweeperStatusMixin: shared "last run" bookkeeping for the reasoning
sweepers (ContradictionSweeper, InferenceStalenessSweeper,
ProvenanceStalenessSweeper, TemporalStalenessSweeper,
GraphFreshnessSweeper, GraphAutoUpdateSweeper, GraphHealthSweeper).

Factored out because every sweeper's ``_run()`` loop already has the
identical shape (see any of the modules above)::

    async def _run(self) -> None:
        while self._running:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(...)
            await asyncio.sleep(self._interval_seconds)

but none of them retain *what happened* on the last pass anywhere --
``sweep_once()``'s return value is discarded by the loop, and a raised
exception is only logged, never recorded on the instance. That's fine
for the sweeper's own purpose (it doesn't need its own history to do
its job), but it means there is currently no way for anything outside
the sweeper -- e.g. an ``agent_status`` MCP tool -- to answer "is this
sweeper actually running, and did its last pass succeed?" without
tailing server logs.

This mixin adds exactly the three fields needed to answer that,
independent of each sweeper's own attribute names (``_interval_seconds``
vs ``_sweep_interval``, ``sweep_once`` vs ``_sweep``, ``list[dict]`` vs
``None`` return) -- callers report success/failure via
``_record_sweep_success``/``_record_sweep_error`` and read the result
back via ``status()``. It does not touch ``start``/``stop``/``_running``
at all; those stay exactly as each sweeper already defines them.

ADR-015 adds one more piece of shared state here: ``_control_lock``, an
``asyncio.Lock`` each sweeper's own ``start()``/``stop()`` wraps its
check-then-act body in, so two concurrent external callers (e.g. two
MCP clients both calling a ``start_agent``/``stop_agent`` tool for the
same ``agent_id``) serialize instead of racing across ``await`` points.
See ADR-015 §4 for the full rationale.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any


class SweeperStatusMixin:
    """Mixed into each reasoning sweeper. See module docstring."""

    def _init_sweeper_status(self) -> None:
        # Must be called explicitly from each sweeper's __init__ --
        # this mixin doesn't define __init__ itself so it doesn't have
        # to worry about cooperative-multiple-inheritance argument
        # forwarding for what is otherwise a plain, single-inherited
        # mixin in every current use.
        self._last_run_at: datetime | None = None
        self._last_run_duration_ms: float | None = None
        self._last_result_count: int | None = None
        self._last_error: str | None = None
        # ADR-015 §4: guards start()/stop()'s check-then-act body against
        # concurrent external callers. In-process state only (self._running,
        # self._task) -- the desired_running storage row is a separate,
        # idempotent upsert that doesn't need this lock (see ADR-015 §4).
        self._control_lock = asyncio.Lock()

    def _record_sweep_success(
        self, started_at: datetime, result: list[Any] | None
    ) -> None:
        self._last_run_at = started_at
        self._last_run_duration_ms = (
            datetime.now(UTC) - started_at
        ).total_seconds() * 1000
        # `result` is None for the one sweeper (InferenceStalenessSweeper)
        # whose sweep method returns nothing -- last_result_count stays
        # None rather than 0, so a caller can distinguish "ran, found
        # nothing" (0) from "this sweeper doesn't report a count" (None).
        self._last_result_count = len(result) if result is not None else None
        self._last_error = None

    def _record_sweep_error(self, started_at: datetime, exc: Exception) -> None:
        self._last_run_at = started_at
        self._last_run_duration_ms = (
            datetime.now(UTC) - started_at
        ).total_seconds() * 1000
        self._last_error = f"{type(exc).__name__}: {exc}"

    def sweeper_status(
        self, *, agent_id: str, running: bool, interval_seconds: float
    ) -> dict[str, Any]:
        """Build the dict returned by cks-mcp's ``agent_status``/``list_agents``
        tools for this sweeper. ``agent_id``/``running``/``interval_seconds``
        are passed in rather than read off ``self`` because their attribute
        names differ across sweepers (see module docstring)."""
        return {
            "agent_id": agent_id,
            "kind": "sweeper",
            "running": running,
            "interval_seconds": interval_seconds,
            "last_run_at": (
                self._last_run_at.isoformat() if self._last_run_at else None
            ),
            "last_run_duration_ms": self._last_run_duration_ms,
            "last_result_count": self._last_result_count,
            "last_error": self._last_error,
        }
