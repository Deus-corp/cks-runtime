"""
Unit tests for ContradictionSweeper.
"""

from __future__ import annotations

import json

import cks
import pytest

from cks_runtime.reasoning.contradiction_sweeper import ContradictionSweeper
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


def _make_session(session_id: str, knowledge_structure: object) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=knowledge_structure, session_id=session_id)
    s.closed = False
    return s


def _make_ks_with_mutual_exclusion_violation() -> object:
    """Earth 'supports' and 'refutes' TheoryX -- both declared mutually
    exclusive by a MutualExclusionRule -- the same shape
    detect_contradictions' own docstring uses as its example."""
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


def _make_ks_with_functional_relation_violation() -> object:
    """Earth 'orbits' both Sun and Mars, but 'orbits' is declared
    functional (single-valued)."""
    objects = [
        {"identity": {"id": "earth", "type": "Concept", "name": "Earth"}, "structure": {}},
        {"identity": {"id": "sun", "type": "Concept", "name": "Sun"}, "structure": {}},
        {"identity": {"id": "mars", "type": "Concept", "name": "Mars"}, "structure": {}},
        {
            "identity": {"id": "rule-2", "type": "FunctionalRelationRule", "name": "single-orbit"},
            "structure": {"relation_type": "orbits"},
        },
        {
            "identity": {"id": "rel-orbits-sun", "type": "Relation", "name": "r1"},
            "structure": {"participants": ["earth", "sun"], "relation_type": "orbits"},
        },
        {
            "identity": {"id": "rel-orbits-mars", "type": "Relation", "name": "r2"},
            "structure": {"participants": ["earth", "mars"], "relation_type": "orbits"},
        },
    ]
    return cks.parse(json.dumps({"objects": objects}))


def _make_plain_ks() -> object:
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


# ---------------------------------------------------------------------------
# sweep_once: detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_mutual_exclusion_violation(storage):
    ks = _make_ks_with_mutual_exclusion_violation()
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["code"] == "CKS-EXT-MUTUAL-EXCLUSION"
    assert payload["severity"] == "error"


@pytest.mark.asyncio
async def test_finds_functional_relation_violation(storage):
    ks = _make_ks_with_functional_relation_violation()
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["code"] == "CKS-EXT-FUNCTIONAL-RELATION"
    assert payload["severity"] == "error"


@pytest.mark.asyncio
async def test_no_rules_or_contradictions_is_a_noop(storage):
    storage.save_session(_make_session("s1", _make_plain_ks()))

    sweeper = ContradictionSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_rules_present_but_no_violation_is_a_noop(storage):
    """A MutualExclusionRule is declared, but no relation pair actually
    trips it -- the additive-by-default property (see cks-core's
    contradiction.py docstring) means this must not be flagged."""
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
    ]
    ks = cks.parse(json.dumps({"objects": objects}))
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    escalated = await sweeper.sweep_once()

    assert escalated == []


# ---------------------------------------------------------------------------
# Outbox enqueueing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueues_contradiction_detected_task(storage):
    ks = _make_ks_with_mutual_exclusion_violation()
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    await sweeper.sweep_once()

    tasks = storage.list_tasks_by_type("contradiction_detected")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_type == "contradiction_detected"
    assert task.session_id == "s1"
    payload = json.loads(task.payload)
    assert payload["code"] == "CKS-EXT-MUTUAL-EXCLUSION"


@pytest.mark.asyncio
async def test_does_not_reescalate_same_contradiction_on_next_sweep(storage):
    ks = _make_ks_with_mutual_exclusion_violation()
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    second = await sweeper.sweep_once()
    assert second == []

    # Only one task was ever written to the outbox.
    tasks = storage.list_tasks_by_type("contradiction_detected", drain=False)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_reescalates_after_contradiction_resolved_and_reintroduced(storage):
    ks = _make_ks_with_mutual_exclusion_violation()
    storage.save_session(_make_session("s1", ks))

    sweeper = ContradictionSweeper(storage)
    first = await sweeper.sweep_once()
    assert len(first) == 1

    # Resolve it: drop the 'refutes' relation.
    resolved_ks = _make_plain_ks()
    storage.save_session(_make_session("s1", resolved_ks))
    second = await sweeper.sweep_once()
    assert second == []

    # Reintroduce the same contradiction: it's newly escalated again,
    # since it dropped out of `_known_diagnostics` once it cleared.
    storage.save_session(_make_session("s1", ks))
    third = await sweeper.sweep_once()
    assert len(third) == 1


# ---------------------------------------------------------------------------
# InMemoryStorage: no-op (no outbox support)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_start_on_inmemory_storage():
    storage = InMemoryStorage()
    sweeper = ContradictionSweeper(storage)

    await sweeper.start()
    try:
        assert sweeper._task is None
    finally:
        await sweeper.stop()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(storage):
    sweeper = ContradictionSweeper(storage, interval_seconds=3600)
    await sweeper.start()
    try:
        assert sweeper._task is not None
    finally:
        await sweeper.stop()
        assert sweeper._task is None
