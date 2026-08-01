from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cks_runtime.gossip.adapter import GossipAdapter


async def gossip_exchange(
    adapter_a: GossipAdapter,
    adapter_b: GossipAdapter,
) -> None:
    """Exchange operations so that both replicas converge."""
    vector_a = await adapter_a.get_local_vector()
    vector_b = await adapter_b.get_local_vector()

    ops_a_to_b = await adapter_a.get_operations_since(vector_b)
    ops_b_to_a = await adapter_b.get_operations_since(vector_a)

    if ops_a_to_b:
        await adapter_b.apply_remote_operations(
            adapter_a.replica_id, ops_a_to_b, vector_a
        )
    if ops_b_to_a:
        await adapter_a.apply_remote_operations(
            adapter_b.replica_id, ops_b_to_a, vector_b
        )