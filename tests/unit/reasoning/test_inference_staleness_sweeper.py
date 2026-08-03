"""
Unit tests for InferenceStalenessSweeper (ADR-009) and the
SQLiteStorage.list_sessions_modified_since storage method it relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import cks
import pytest

from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import InferenceConflictDetected
from cks_runtime.reasoning.inference_staleness_sweeper import InferenceStalenessSweeper
from cks_runtime.session.session import RuntimeSession
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


@pytest.fixture
def event_bus():
    return EventBus()


def _make_plain_ks():
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_conflicting_ks(conclusion: str = "c1"):
    """Two active InferenceSteps sharing a conclusion but disagreeing
    on confidence -- triggers CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT."""
    return cks.parse(
        '{"objects":['
        '{"identity":{"id":"step-1","type":"InferenceStep","name":"step-1"},'
        f'"structure":{{"conclusion":"{conclusion}","confidence":0.9}}}},'
        '{"identity":{"id":"step-2","type":"InferenceStep","name":"step-2"},'
        f'"structure":{{"conclusion":"{conclusion}","confidence":0.2}}}}'
        "]}"
    )


def _make_session(
    session_id: str = "s1", *, knowledge_structure=None, closed: bool = False
) -> RuntimeSession:
    s = RuntimeSession(
        knowledge_structure=knowledge_structure or _make_plain_ks(),
        session_id=session_id,
    )
    s.closed = closed
    return s


def _backdate(storage: SQLiteStorage, session_id: str, days: int) -> None:
    """Manually push modified_at back in time (mirrors the GC test helper)."""
    storage._conn.execute(
        "UPDATE sessions SET modified_at = datetime('now', ?) WHERE session_id = ?",
        (f"-{days} days", session_id),
    )
    storage._conn.commit()


# ---------------------------------------------------------------------------
# SQLiteStorage.list_sessions_modified_since
# ---------------------------------------------------------------------------


def test_list_sessions_modified_since_returns_recent(storage):
    s1 = _make_session("s1")
    s2 = _make_session("s2")
    storage.save_session(s1)
    storage.save_session(s2)

    _backdate(storage, "s1", 2)  # old: 2 days ago
    # s2 is fresh (just saved, modified_at = now)

    watermark = datetime.now(UTC) - timedelta(hours=1)
    recent = storage.list_sessions_modified_since(watermark)
    ids = {s.session_id for s in recent}

    assert "s2" in ids
    assert "s1" not in ids


def test_list_sessions_modified_since_includes_open_and_closed(storage):
    s_open = _make_session("open", closed=False)
    s_closed = _make_session("closed", closed=True)
    storage.save_session(s_open)
    storage.save_session(s_closed)

    watermark = datetime.now(UTC) - timedelta(hours=1)
    ids = {s.session_id for s in storage.list_sessions_modified_since(watermark)}

    # Unlike GC's list_sessions_modified_before, closed-ness is
    # irrelevant here -- both must be returned.
    assert ids == {"open", "closed"}


def test_list_sessions_modified_since_empty_when_all_before_watermark(storage):
    s = _make_session("s1")
    storage.save_session(s)
    _backdate(storage, "s1", 5)

    watermark = datetime.now(UTC) - timedelta(hours=1)
    assert storage.list_sessions_modified_since(watermark) == []


def test_list_sessions_modified_since_respects_limit(storage):
    for i in range(10):
        s = _make_session(f"s{i}")
        storage.save_session(s)

    watermark = datetime.now(UTC) - timedelta(hours=1)
    result = storage.list_sessions_modified_since(watermark, limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# InferenceStalenessSweeper behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweeper_publishes_conflict_for_new_finding(storage, event_bus):
    session = _make_session("s1", knowledge_structure=_make_conflicting_ks())
    storage.save_session(session)

    received: list[InferenceConflictDetected] = []
    event_bus.subscribe(InferenceConflictDetected, received.append)

    sweeper = InferenceStalenessSweeper(storage, event_bus)
    await sweeper.run_once()

    assert len(received) == 1
    event = received[0]
    assert event.session_id == "s1"
    codes = {d["code"] for d in event.diagnostics}
    assert "CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT" in codes


@pytest.mark.asyncio
async def test_sweeper_ignores_session_without_conflict(storage, event_bus):
    session = _make_session("s1", knowledge_structure=_make_plain_ks())
    storage.save_session(session)

    received: list[InferenceConflictDetected] = []
    event_bus.subscribe(InferenceConflictDetected, received.append)

    sweeper = InferenceStalenessSweeper(storage, event_bus)
    await sweeper.run_once()

    assert received == []


@pytest.mark.asyncio
async def test_sweeper_does_not_republish_known_diagnostic(storage, event_bus):
    session = _make_session("s1", knowledge_structure=_make_conflicting_ks())
    storage.save_session(session)

    received: list[InferenceConflictDetected] = []
    event_bus.subscribe(InferenceConflictDetected, received.append)

    sweeper = InferenceStalenessSweeper(storage, event_bus)
    await sweeper.run_once()
    await sweeper.run_once()  # same unresolved conflict, second sweep

    assert len(received) == 1  # not re-published


@pytest.mark.asyncio
async def test_sweeper_republishes_after_new_distinct_conflict(storage, event_bus):
    session = _make_session("s1", knowledge_structure=_make_conflicting_ks("c1"))
    storage.save_session(session)

    received: list[InferenceConflictDetected] = []
    event_bus.subscribe(InferenceConflictDetected, received.append)

    sweeper = InferenceStalenessSweeper(storage, event_bus)
    await sweeper.run_once()
    assert len(received) == 1

    # A second, distinct conflict (different conclusion, different step ids
    # so the dedup key (code, location) changes) must publish again.
    session.knowledge_structure = _make_conflicting_ks_with_different_steps(
        "c2", "step-3", "step-4"
    )
    storage.save_session(session)
    await sweeper.run_once()

    assert len(received) == 2
    new_codes_locations = {
        (d["code"], d["location"]) for d in received[1].diagnostics
    }
    assert ("CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT", "step-3") in new_codes_locations


@pytest.mark.asyncio
async def test_sweeper_noop_on_unsupported_storage(event_bus):
    """InferenceStalenessSweeper.start() should not raise on InMemoryStorage."""
    mem = InMemoryStorage()
    sweeper = InferenceStalenessSweeper(mem, event_bus)
    await sweeper.start()
    await sweeper.stop()


@pytest.mark.asyncio
async def test_sweeper_run_once_on_unsupported_storage_finds_nothing(event_bus):
    """InMemoryStorage's base-class list_sessions_modified_since default
    always returns [], so a direct run_once() is a harmless no-op."""
    mem = InMemoryStorage()
    received: list[InferenceConflictDetected] = []
    event_bus.subscribe(InferenceConflictDetected, received.append)

    sweeper = InferenceStalenessSweeper(mem, event_bus)
    await sweeper.run_once()

    assert received == []


@pytest.mark.asyncio
async def test_sweeper_lifecycle_start_stop(storage, event_bus):
    sweeper = InferenceStalenessSweeper(storage, event_bus, sweep_interval=0.01)
    await sweeper.start()
    assert sweeper._running is True
    await sweeper.stop()
    assert sweeper._running is False


def _make_conflicting_ks_with_different_steps(conclusion: str, step_a: str, step_b: str):
    return cks.parse(
        '{"objects":['
        f'{{"identity":{{"id":"{step_a}","type":"InferenceStep","name":"{step_a}"}},'
        f'"structure":{{"conclusion":"{conclusion}","confidence":0.9}}}},'
        f'{{"identity":{{"id":"{step_b}","type":"InferenceStep","name":"{step_b}"}},'
        f'"structure":{{"conclusion":"{conclusion}","confidence":0.2}}}}'
        "]}"
    )