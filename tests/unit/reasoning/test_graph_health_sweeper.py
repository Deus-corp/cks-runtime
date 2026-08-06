"""
Unit tests for GraphHealthSweeper.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import cks
import pytest

from cks_runtime.reasoning.graph_health_sweeper import (
    DEFAULT_MIN_SCORE,
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    GraphHealthSweeper,
)
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage

# pyproject.toml sets asyncio_mode = "auto" for this project's test suite
# (see test_graph_freshness_sweeper.py/test_graph_auto_update_sweeper.py,
# which use @pytest.mark.asyncio the same way); mirroring that here.

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def _plain_ks() -> object:
    """A structure with no Component/VerificationRecord/rule objects at
    all -- version_freshness, verification_coverage default to 1.0
    (nothing to check), contradictions score 1.0 (nothing to violate)."""
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_ks_with_mutual_exclusion_violation() -> object:
    """Same shape as ContradictionSweeper's own test fixture: Earth
    'supports' and 'refutes' TheoryX, both declared mutually exclusive."""
    objects = [
        {"identity": {"id": "earth", "type": "Concept", "name": "Earth"}, "structure": {}},
        {"identity": {"id": "theory-x", "type": "Concept", "name": "TheoryX"}, "structure": {}},
        {
            "identity": {"id": "rule-1", "type": "MutualExclusionRule", "name": "no-support-and-refute"},
            "structure": {"relation_type_a": "supports", "relation_type_b": "refutes"},
        },
        {
            "identity": {"id": "rel-supports", "type": "Relation", "name": "r1"},
            "structure": {"participants": ["earth", "theory-x"], "relation_type": "supports"},
        },
        {
            "identity": {"id": "rel-refutes", "type": "Relation", "name": "r2"},
            "structure": {"participants": ["earth", "theory-x"], "relation_type": "refutes"},
        },
    ]
    return cks.parse(json.dumps({"objects": objects}))


def _make_session(session_id: str, knowledge_structure: object) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=knowledge_structure, session_id=session_id)
    s.closed = False
    return s


def _register(storage: SQLiteStorage, name: str, session_id: str, ks: object) -> None:
    storage.save_session(_make_session(session_id, ks))
    storage.register_graph(name, session_id)


def _backdate_graph(storage: SQLiteStorage, name: str, days: int) -> None:
    storage._conn.execute(
        "UPDATE graph_registry SET updated_at = datetime('now', ?) WHERE name = ?",
        (f"-{days} days", name),
    )
    storage._conn.commit()


def _outbox_payloads(storage: SQLiteStorage, task_type: str = "health_check") -> list[dict]:
    rows = storage._conn.execute(
        "SELECT payload FROM cks_outbox_tasks WHERE task_type = ?", (task_type,)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


_NO_NETWORK = patch(
    "cks_runtime.reasoning.graph_health_sweeper._fetch_version_sync",
    side_effect=AssertionError("GraphHealthSweeper attempted a version fetch"),
)


# ---------------------------------------------------------------------------
# sweep_once: core scoring/escalation behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_graph_not_escalated(storage):
    _register(storage, "g1", "s1", _plain_ks())

    sweeper = GraphHealthSweeper(storage)
    with _NO_NETWORK:
        escalated = await sweeper.sweep_once()

    assert escalated == []
    assert _outbox_payloads(storage) == []


@pytest.mark.asyncio
async def test_stale_and_contradictory_graph_escalated(storage):
    _register(storage, "g1", "s1", _make_ks_with_mutual_exclusion_violation())
    _backdate_graph(storage, "g1", days=10)  # TTL freshness -> 0.0

    # score = 0.3*1.0 (no components) + 0.1*0.0 (stale) + 0.3*0.0
    #         (contradiction) + 0.2*1.0 (no verification records)
    #         + 0.1*1.0 (no dead-letter tasks) = 0.6, below default 0.7
    sweeper = GraphHealthSweeper(storage, ttl_seconds=7 * 24 * 3600)
    with _NO_NETWORK:
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["name"] == "g1"
    assert payload["session_id"] == "s1"
    assert payload["health_score"] == pytest.approx(0.6)
    assert payload["metrics"]["contradictions"] == 0.0
    assert payload["metrics"]["ttl_freshness"] == 0.0
    assert payload["min_score"] == DEFAULT_MIN_SCORE

    outbox = _outbox_payloads(storage)
    assert len(outbox) == 1
    assert outbox[0]["name"] == "g1"


@pytest.mark.asyncio
async def test_custom_min_score_threshold(storage):
    _register(storage, "g1", "s1", _make_ks_with_mutual_exclusion_violation())
    _backdate_graph(storage, "g1", days=10)

    # Same graph (score 0.6) is not escalated under a looser threshold.
    sweeper = GraphHealthSweeper(storage, min_score=0.5)
    with _NO_NETWORK:
        escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_no_graphs_is_a_noop(storage):
    sweeper = GraphHealthSweeper(storage)
    escalated = await sweeper.sweep_once()
    assert escalated == []


@pytest.mark.asyncio
async def test_session_not_available_skipped(storage):
    # Registered graph pointing at a session_id never saved.
    storage.register_graph("g1", "missing-session")

    sweeper = GraphHealthSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_reescalate_same_graph_on_next_sweep(storage):
    _register(storage, "g1", "s1", _make_ks_with_mutual_exclusion_violation())
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphHealthSweeper(storage)
    with _NO_NETWORK:
        first = await sweeper.sweep_once()
        second = await sweeper.sweep_once()

    assert len(first) == 1
    assert second == []
    assert len(_outbox_payloads(storage)) == 1


@pytest.mark.asyncio
async def test_reescalates_after_recovery_then_degrades_again(storage):
    _register(storage, "g1", "s1", _make_ks_with_mutual_exclusion_violation())
    _backdate_graph(storage, "g1", days=10)

    sweeper = GraphHealthSweeper(storage)
    with _NO_NETWORK:
        first = await sweeper.sweep_once()
    assert len(first) == 1

    # Graph recovers: re-registered with a clean structure, freshly
    # updated.
    _register(storage, "g1", "s1", _plain_ks())
    with _NO_NETWORK:
        second = await sweeper.sweep_once()
    assert second == []

    # Degrades again.
    _register(storage, "g1", "s1", _make_ks_with_mutual_exclusion_violation())
    _backdate_graph(storage, "g1", days=10)
    with _NO_NETWORK:
        third = await sweeper.sweep_once()
    assert len(third) == 1


# ---------------------------------------------------------------------------
# InMemoryStorage: no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_op_on_in_memory_storage():
    storage = InMemoryStorage()
    sweeper = GraphHealthSweeper(storage)

    # supports_outbox is False for InMemoryStorage, so start() should
    # not spin up a background task at all.
    await sweeper.start()
    assert sweeper._task is None
    await sweeper.stop()

    # sweep_once still runs safely (list_graphs is a no-op returning
    # []), it just never has anything to escalate.
    escalated = await sweeper.sweep_once()
    assert escalated == []


# ---------------------------------------------------------------------------
# Dead-letter component of the score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_tasks_lower_score(storage):
    _register(storage, "g1", "s1", _plain_ks())
    storage.enqueue_task(task_type="inference_conflict", session_id="s1", payload="{}")
    task = storage.list_tasks_by_type("inference_conflict", drain=False)[0]
    storage.dead_letter_outbox_task(task.task_id, "boom")

    sweeper = GraphHealthSweeper(storage, min_score=1.0)  # force escalation on any imperfection
    with _NO_NETWORK:
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["metrics"]["dead_letter"] == 0.5


# ---------------------------------------------------------------------------
# start/stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_lifecycle(storage):
    sweeper = GraphHealthSweeper(storage, interval_seconds=1000)
    assert sweeper._task is None

    await sweeper.start()
    assert sweeper._task is not None
    assert sweeper._running is True

    # start() again is a no-op while already running.
    task_before = sweeper._task
    await sweeper.start()
    assert sweeper._task is task_before

    await sweeper.stop()
    assert sweeper._task is None
    assert sweeper._running is False


@pytest.mark.asyncio
async def test_does_not_start_on_unsupported_storage():
    storage = InMemoryStorage()
    sweeper = GraphHealthSweeper(storage)

    await sweeper.start()
    try:
        assert sweeper._task is None
    finally:
        await sweeper.stop()


def test_default_interval_and_min_score():
    assert DEFAULT_SWEEP_INTERVAL_SECONDS == 3600
    assert DEFAULT_MIN_SCORE == 0.7
