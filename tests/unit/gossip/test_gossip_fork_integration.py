"""
Integration tests for ADR-013 Stage 2: GossipAdapter advancing
MV-Register pointers and escalating forks (_handle_fork /
_detect_and_handle_fork) via CRDTStore.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.crdt.crdt_store import InMemoryCRDTStore
from cks_runtime.crdt.version_vector import VersionVector
from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import CRDTForkDetected
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.runtime import Runtime

pytestmark = pytest.mark.asyncio


async def _adapter_with_store() -> tuple[GossipAdapter, InMemoryCRDTStore]:
    runtime = await Runtime.create(core=CksCoreAdapter())
    store = InMemoryCRDTStore()
    adapter = GossipAdapter(runtime, replica_id=str(uuid4()), crdt_store=store)
    return adapter, store


async def test_detect_and_handle_fork_noop_when_single_pointer():
    adapter, store = await _adapter_with_store()
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")

    await adapter._detect_and_handle_fork("head")

    assert store.list_pending_forks() == []


async def test_detect_and_handle_fork_escalates_concurrent_pointers():
    adapter, store = await _adapter_with_store()
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")
    store.update_pointer("head", "obj-b", VersionVector(clocks={"n2": 1}), "n2")

    await adapter._detect_and_handle_fork("head")

    pending = store.list_pending_forks()
    assert len(pending) == 1
    assert pending[0]["pointer_key"] == "head"
    assert set(pending[0]["conflicting_object_ids"]) == {"obj-a", "obj-b"}


async def test_handle_fork_publishes_event_on_bus():
    runtime = await Runtime.create(core=CksCoreAdapter())
    store = InMemoryCRDTStore()
    bus = EventBus()
    adapter = GossipAdapter(runtime, replica_id=str(uuid4()), event_bus=bus, crdt_store=store)

    received: list[CRDTForkDetected] = []

    async def _on_fork(event: CRDTForkDetected) -> None:
        received.append(event)

    bus.subscribe(CRDTForkDetected, _on_fork)

    await adapter._handle_fork("head", ["obj-a", "obj-b"], [{"n1": 1}, {"n2": 1}])

    assert len(received) == 1
    assert received[0].pointer_key == "head"
    assert set(received[0].conflicting_object_ids) == {"obj-a", "obj-b"}
    assert received[0].conflict_event_id
    # the same event_id is resolvable back on the store
    assert store.list_pending_forks()[0]["event_id"] == received[0].conflict_event_id


async def test_handle_fork_is_noop_without_crdt_store():
    runtime = await Runtime.create(core=CksCoreAdapter())
    adapter = GossipAdapter(runtime, replica_id=str(uuid4()))
    # must not raise even though there's no crdt_store configured
    await adapter._handle_fork("head", ["obj-a", "obj-b"], [{"n1": 1}, {"n2": 1}])