"""
Unit tests for GossipAdapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.versioning.version_vector import VersionVector

pytestmark = pytest.mark.asyncio


async def test_gossip_adapter_has_replica_id():
    storage = MagicMock()
    adapter = GossipAdapter(storage, MagicMock(), "replica-42")
    assert adapter.replica_id == "replica-42"


async def test_get_local_vector_returns_empty_by_default():
    adapter = GossipAdapter(MagicMock(), MagicMock(), "r1")
    vector = await adapter.get_local_vector()
    assert isinstance(vector, VersionVector)
    assert vector.clocks == {}


async def test_get_operations_since_delegates_to_storage():
    storage = MagicMock()
    storage.fetch_operations_since = AsyncMock(return_value=[])
    adapter = GossipAdapter(storage, MagicMock(), "r1")
    result = await adapter.get_operations_since(VersionVector())
    assert result == []


async def test_apply_remote_operations_raises_not_implemented():
    adapter = GossipAdapter(MagicMock(), MagicMock(), "r1")
    with pytest.raises(NotImplementedError, match="OperationExecutor"):
        await adapter.apply_remote_operations(
            "other",
            [MagicMock()],
            VersionVector(),
        )


async def test_apply_remote_operations_empty_list_returns_true():
    adapter = GossipAdapter(MagicMock(), MagicMock(), "r1")
    result = await adapter.apply_remote_operations("other", [], VersionVector())
    assert result is True


async def test_apply_remote_operations_with_operations_raises_not_implemented():
    adapter = GossipAdapter(MagicMock(), MagicMock(), "r1")
    with pytest.raises(NotImplementedError):
        await adapter.apply_remote_operations(
            "other",
            [MagicMock()],  # non-empty list triggers the stub
            VersionVector(),
        )