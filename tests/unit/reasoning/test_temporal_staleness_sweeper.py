"""
Unit tests for TemporalStalenessSweeper (ADR-011).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import cks
import pytest

from cks_runtime.reasoning.temporal_staleness_sweeper import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    TemporalStalenessSweeper,
)
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_ks_with_object(
    *,
    object_id: str = "fact-1",
    object_type: str = "Claim",
    valid_until: str | None,
) -> object:
    """Build a KnowledgeStructure with a single object, optionally
    carrying a `valid_until` field -- the shape TemporalValidityConstraint
    (cks-core ADR-003) inspects."""
    obj_structure: dict[str, object] = {}
    if valid_until is not None:
        obj_structure["valid_until"] = valid_until

    obj = {
        "identity": {"id": object_id, "type": object_type, "name": object_id},
        "structure": obj_structure,
    }
    return cks.parse(json.dumps({"objects": [obj]}))


def _make_plain_ks() -> object:
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_session(session_id: str, knowledge_structure: object) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=knowledge_structure, session_id=session_id)
    s.closed = False
    return s


# ---------------------------------------------------------------------------
# sweep_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_expired_fact(storage):
    now = datetime.now(UTC)
    expired = _iso(now - timedelta(days=1))
    ks = _make_ks_with_object(valid_until=expired)
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["object_id"] == "fact-1"
    assert payload["object_type"] == "Claim"
    assert payload["valid_until"] == expired
    assert payload["reason"] == "valid_until_expired"


@pytest.mark.asyncio
async def test_does_not_flag_fresh_fact(storage):
    now = datetime.now(UTC)
    future = _iso(now + timedelta(days=1))
    ks = _make_ks_with_object(valid_until=future)
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_missing_valid_until_is_a_noop(storage):
    ks = _make_ks_with_object(valid_until=None)
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_plain_structure_without_temporal_fields_is_a_noop(storage):
    storage.save_session(_make_session("s1", _make_plain_ks()))

    sweeper = TemporalStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_does_not_crash_on_malformed_valid_until(storage):
    ks = _make_ks_with_object(valid_until="not-a-timestamp")
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    # TemporalValidityConstraint reports malformed valid_until as an
    # ERROR diagnostic under the same code, not a WARNING -- the
    # sweeper only escalates WARNINGs, so this should simply be
    # ignored rather than raising.
    escalated = await sweeper.sweep_once()

    assert escalated == []


# ---------------------------------------------------------------------------
# Outbox enqueueing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueues_temporal_conflict_task(storage):
    now = datetime.now(UTC)
    expired = _iso(now - timedelta(days=1))
    ks = _make_ks_with_object(valid_until=expired)
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    await sweeper.sweep_once()

    tasks = storage.list_tasks_by_type("temporal_conflict")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == "temporal_conflict"
    assert task.session_id == "s1"
    payload = json.loads(task.payload)
    assert payload["object_id"] == "fact-1"
    assert payload["reason"] == "valid_until_expired"


@pytest.mark.asyncio
async def test_does_not_reescalate_same_fact_on_next_sweep(storage):
    now = datetime.now(UTC)
    expired = _iso(now - timedelta(days=1))
    ks = _make_ks_with_object(valid_until=expired)
    storage.save_session(_make_session("s1", ks))

    sweeper = TemporalStalenessSweeper(storage)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    second = await sweeper.sweep_once()
    assert second == []

    # Only one task was ever written to the outbox.
    tasks = storage.list_tasks_by_type("temporal_conflict", drain=False)
    assert len(tasks) == 1


# ---------------------------------------------------------------------------
# start()/stop() and storage capability detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_start_on_unsupported_storage():
    storage = InMemoryStorage()
    sweeper = TemporalStalenessSweeper(storage)

    await sweeper.start()
    try:
        assert sweeper._task is None
    finally:
        await sweeper.stop()


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(storage):
    sweeper = TemporalStalenessSweeper(storage, interval_seconds=3600)
    await sweeper.start()
    try:
        assert sweeper._task is not None
        assert not sweeper._task.done()
    finally:
        await sweeper.stop()
    assert sweeper._task is None


def test_default_interval_matches_adr_011():
    assert DEFAULT_SWEEP_INTERVAL_SECONDS == 3600
