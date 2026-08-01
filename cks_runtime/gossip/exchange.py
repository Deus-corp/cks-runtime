"""
In-process gossip exchange between two replicas.

This simulates what a real ``GossipTransport`` (out of scope here --
see ADR-008's Non-Goals) would do over the wire: fetch each side's
current snapshot of a shared session and hand it to the other side's
``GossipAdapter.apply_remote_session``. Useful directly for two
``Runtime`` instances in the same process (e.g. tests, or a
single-process simulation of a swarm), and as the reference sequence
a real transport should reproduce once one exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cks_runtime.gossip.adapter import GossipAdapter


async def gossip_exchange(
    session_id: str,
    adapter_a: GossipAdapter,
    adapter_b: GossipAdapter,
) -> None:
    """
    Exchange session state for ``session_id`` so both replicas
    converge, applying each other's snapshot only when it isn't
    already dominated by the receiving side.
    """
    session_a = adapter_a._runtime.get_session(session_id)
    session_b = adapter_b._runtime.get_session(session_id)

    if session_a is not None:
        await adapter_b.apply_remote_session(session_a)
    if session_b is not None:
        await adapter_a.apply_remote_session(session_b)