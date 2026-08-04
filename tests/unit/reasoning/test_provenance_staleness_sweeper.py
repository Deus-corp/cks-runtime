"""
Unit tests for ProvenanceStalenessSweeper (ADR-010).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import cks
import pytest

from cks_runtime.reasoning.provenance_staleness_sweeper import (
    DEFAULT_PROVENANCE_TTL_SECONDS,
    ProvenanceStalenessSweeper,
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


def _make_ks_with_record(
    *,
    record_id: str = "vr-1",
    subject_id: str = "doc-1",
    checked_at: str | None,
    subject_url: str | None = "https://example.com/a",
    checked_via: str = "automated_http_check",
) -> object:
    """Build a KnowledgeStructure with a Document (subject) and a
    VerificationRecord linked via verified_by -- the same shape
    verify_source's handler produces."""
    subject_structure: dict[str, object] = {}
    if subject_url is not None:
        subject_structure["url"] = subject_url

    record_structure: dict[str, object] = {"checked_via": checked_via}
    if checked_at is not None:
        record_structure["checked_at"] = checked_at

    doc = {
        "identity": {"id": subject_id, "type": "Document", "name": subject_id},
        "structure": subject_structure,
    }
    record = {
        "identity": {"id": record_id, "type": "VerificationRecord", "name": record_id},
        "structure": record_structure,
    }
    relation = {
        "identity": {"id": f"rel-{record_id}", "type": "Relation", "name": "r"},
        "structure": {
            "participants": [subject_id, record_id],
            "relation_type": "verified_by",
        },
    }
    return cks.parse(json.dumps({"objects": [doc, record, relation]}))


def _make_plain_ks() -> object:
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_session(session_id: str, knowledge_structure: object) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=knowledge_structure, session_id=session_id)
    s.closed = False
    return s


def _backdate(storage: SQLiteStorage, session_id: str, days: int) -> None:
    storage._conn.execute(
        "UPDATE sessions SET modified_at = datetime('now', ?) WHERE session_id = ?",
        (f"-{days} days", session_id),
    )
    storage._conn.commit()


# ---------------------------------------------------------------------------
# sweep_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_expired_verification_record(storage):
    now = datetime.now(UTC)
    stale_checked_at = _iso(now - timedelta(days=40))
    ks = _make_ks_with_record(checked_at=stale_checked_at)
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["record_id"] == "vr-1"
    assert payload["subject_id"] == "doc-1"
    assert payload["source_url"] == "https://example.com/a"
    assert payload["checked_at"] == stale_checked_at
    assert payload["reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_does_not_flag_fresh_record(storage):
    now = datetime.now(UTC)
    fresh_checked_at = _iso(now - timedelta(days=1))
    ks = _make_ks_with_record(checked_at=fresh_checked_at)
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_respects_custom_ttl(storage):
    now = datetime.now(UTC)
    checked_at = _iso(now - timedelta(hours=2))
    ks = _make_ks_with_record(checked_at=checked_at)
    storage.save_session(_make_session("s1", ks))

    # 1 hour TTL: a record checked 2 hours ago is stale.
    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=3600)
    escalated = await sweeper.sweep_once()
    assert len(escalated) == 1

    # 3 hour TTL: the same record is still fresh.
    storage2 = SQLiteStorage(":memory:")
    try:
        storage2.save_session(_make_session("s1", ks))
        sweeper2 = ProvenanceStalenessSweeper(storage2, ttl_seconds=3 * 3600)
        escalated2 = await sweeper2.sweep_once()
        assert escalated2 == []
    finally:
        storage2.clear()


@pytest.mark.asyncio
async def test_no_verification_records_is_a_noop(storage):
    storage.save_session(_make_session("s1", _make_plain_ks()))

    sweeper = ProvenanceStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_malformed_checked_at_is_skipped(storage):
    ks = _make_ks_with_record(checked_at="not-a-timestamp")
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_missing_checked_at_is_skipped(storage):
    ks = _make_ks_with_record(checked_at=None)
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_subject_without_url_omits_source_url(storage):
    now = datetime.now(UTC)
    ks = _make_ks_with_record(
        checked_at=_iso(now - timedelta(days=40)), subject_url=None
    )
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["source_url"] is None


# ---------------------------------------------------------------------------
# Outbox enqueueing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueues_provenance_conflict_task(storage):
    now = datetime.now(UTC)
    ks = _make_ks_with_record(checked_at=_iso(now - timedelta(days=40)))
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    await sweeper.sweep_once()

    tasks = storage.list_tasks_by_type("provenance_conflict")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == "provenance_conflict"
    assert task.session_id == "s1"
    payload = json.loads(task.payload)
    assert payload["record_id"] == "vr-1"
    assert payload["reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_does_not_reescalate_same_record_on_next_sweep(storage):
    now = datetime.now(UTC)
    ks = _make_ks_with_record(checked_at=_iso(now - timedelta(days=40)))
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    second = await sweeper.sweep_once()
    assert second == []

    # Only one task was ever written to the outbox.
    tasks = storage.list_tasks_by_type("provenance_conflict", drain=False)
    assert len(tasks) == 1


# ---------------------------------------------------------------------------
# No HTTP / no outbound I/O
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_makes_no_network_calls(storage, monkeypatch):
    """The sweeper is detection-only -- it must never perform outbound
    I/O itself (see ADR-010). There is nothing to mock because nothing
    in this module imports an HTTP client; this test asserts that
    remains true by failing loudly if one is ever introduced."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("ProvenanceStalenessSweeper attempted a network call")

    monkeypatch.setattr(socket, "socket", _blocked)

    now = datetime.now(UTC)
    ks = _make_ks_with_record(checked_at=_iso(now - timedelta(days=40)))
    storage.save_session(_make_session("s1", ks))

    sweeper = ProvenanceStalenessSweeper(storage, ttl_seconds=30 * 24 * 3600)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1


# ---------------------------------------------------------------------------
# start()/stop() and storage capability detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_start_on_unsupported_storage():
    storage = InMemoryStorage()
    sweeper = ProvenanceStalenessSweeper(storage)

    await sweeper.start()
    try:
        assert sweeper._task is None
    finally:
        await sweeper.stop()


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(storage):
    sweeper = ProvenanceStalenessSweeper(storage, interval_seconds=3600)
    await sweeper.start()
    try:
        assert sweeper._task is not None
        assert not sweeper._task.done()
    finally:
        await sweeper.stop()
    assert sweeper._task is None


def test_default_ttl_matches_adr_010():
    assert DEFAULT_PROVENANCE_TTL_SECONDS == 30 * 24 * 3600