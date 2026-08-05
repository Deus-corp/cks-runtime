"""
Unit tests for GraphFreshnessSweeper (Memory Agent v2).
"""

from __future__ import annotations

import json

import pytest

from cks_runtime.reasoning.graph_freshness_sweeper import (
    DEFAULT_GRAPH_FRESHNESS_TTL_SECONDS,
    GraphFreshnessSweeper,
)
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def _backdate_graph(storage: SQLiteStorage, name: str, days: int) -> None:
    storage._conn.execute(
        "UPDATE graph_registry SET updated_at = datetime('now', ?) WHERE name = ?",
        (f"-{days} days", name),
    )
    storage._conn.commit()


# ---------------------------------------------------------------------------
# sweep_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_outdated_graph(storage):
    storage.register_graph("g1", "s1", "desc", "tag1")
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["name"] == "g1"
    assert payload["session_id"] == "s1"
    assert payload["reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_does_not_flag_fresh_graph(storage):
    storage.register_graph("g1", "s1")

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_respects_custom_ttl(storage):
    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=2)

    # 1 day TTL: a graph updated 2 days ago is outdated.
    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=24 * 3600)
    escalated = await sweeper.sweep_once()
    assert len(escalated) == 1

    # 3 day TTL: the same graph is still fresh.
    storage2 = SQLiteStorage(":memory:")
    try:
        storage2.register_graph("g1", "s1")
        _backdate_graph(storage2, "g1", days=2)
        sweeper2 = GraphFreshnessSweeper(storage2, ttl_seconds=3 * 24 * 3600)
        escalated2 = await sweeper2.sweep_once()
        assert escalated2 == []
    finally:
        storage2.clear()


@pytest.mark.asyncio
async def test_no_graphs_is_a_noop(storage):
    sweeper = GraphFreshnessSweeper(storage)
    escalated = await sweeper.sweep_once()
    assert escalated == []


@pytest.mark.asyncio
async def test_multiple_graphs_mixed_freshness(storage):
    storage.register_graph("fresh", "s1")
    storage.register_graph("stale", "s2")
    _backdate_graph(storage, "stale", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["name"] == "stale"


# ---------------------------------------------------------------------------
# Outbox enqueueing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueues_graph_outdated_task(storage):
    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    await sweeper.sweep_once()

    tasks = storage.list_tasks_by_type("graph_outdated")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == "graph_outdated"
    assert task.session_id == "s1"
    payload = json.loads(task.payload)
    assert payload["name"] == "g1"
    assert payload["reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_does_not_reescalate_same_graph_on_next_sweep(storage):
    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    second = await sweeper.sweep_once()
    assert second == []

    # Only one task was ever written to the outbox.
    tasks = storage.list_tasks_by_type("graph_outdated", drain=False)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_reescalates_after_graph_refreshed_then_stale_again(storage):
    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    # Graph gets refreshed (re-registered), clearing its staleness.
    storage.register_graph("g1", "s1")
    second = await sweeper.sweep_once()
    assert second == []

    # It goes stale again.
    _backdate_graph(storage, "g1", days=10)
    third = await sweeper.sweep_once()
    assert len(third) == 1


# ---------------------------------------------------------------------------
# Sweeper does not update the graph itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_does_not_modify_registry(storage):
    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=10)
    before = storage.get_graph("g1")

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    await sweeper.sweep_once()

    after = storage.get_graph("g1")
    assert before == after


@pytest.mark.asyncio
async def test_sweep_makes_no_network_calls(storage, monkeypatch):
    """The sweeper is detection-only -- it must never perform outbound
    I/O itself. There is nothing to mock because nothing in this
    module imports an HTTP client; this test asserts that remains
    true by failing loudly if one is ever introduced."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("GraphFreshnessSweeper attempted a network call")

    monkeypatch.setattr(socket, "socket", _blocked)

    storage.register_graph("g1", "s1")
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphFreshnessSweeper(storage, ttl_seconds=7 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1


# ---------------------------------------------------------------------------
# start()/stop() and storage capability detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_start_on_unsupported_storage():
    storage = InMemoryStorage()
    sweeper = GraphFreshnessSweeper(storage)

    await sweeper.start()
    try:
        assert sweeper._task is None
    finally:
        await sweeper.stop()


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(storage):
    sweeper = GraphFreshnessSweeper(storage, interval_seconds=3600)
    await sweeper.start()
    try:
        assert sweeper._task is not None
        assert not sweeper._task.done()
    finally:
        await sweeper.stop()
    assert sweeper._task is None


def test_default_ttl_is_seven_days():
    assert DEFAULT_GRAPH_FRESHNESS_TTL_SECONDS == 7 * 24 * 3600
