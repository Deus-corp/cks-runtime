"""
VersionVector for the CRDT adapter (ADR-013, Stage 1).

This is deliberately a *separate* type from
``cks_runtime.versioning.version_vector.VersionVector`` (ADR-007 / used
by ``GossipAdapter`` today), not a subclass or a re-export. The
ADR-007 vector is anchored to ``RuntimeSession.metadata`` (see
``from_metadata``/``to_metadata``) and its ``dominates``/``absorb``
API is tailored to the fast-forward / three-way-merge decision in
``MergeOperation``. The CRDT layer's vector instead tracks, per
``node_id``, "how many CRDT records this node has locally produced"
independent of any RuntimeSession -- it is persisted in the
``cks_crdt_state`` table (see ``crdt_store.py``), not in session
metadata, and it never needs to decide "does A dominate B" for a
merge; it only needs ``merge`` (a symmetric per-key max) and
``seen``/``observe`` for gossip peers to compare progress. Keeping it
separate avoids overloading the ADR-007 type with a second, unrelated
persistence contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VersionVector:
    """
    Per-node logical clocks, used by the CRDT layer to track how much
    of each node's local history has been observed.

    ``clocks`` maps ``node_id -> highest clock value observed from
    that node``. A missing node_id is equivalent to clock 0.
    """

    clocks: dict[str, int] = field(default_factory=dict)

    def bump(self, node_id: str) -> int:
        """
        Record one more local event from ``node_id`` and return the
        new clock value for it.
        """
        new_value = self.clocks.get(node_id, 0) + 1
        self.clocks[node_id] = new_value
        return new_value

    def observe(self, node_id: str, clock: int) -> None:
        """
        Record having seen up to ``clock`` from ``node_id``. Never
        moves the recorded clock backwards -- observing an older
        clock than what's already known is a no-op.
        """
        if clock > self.clocks.get(node_id, 0):
            self.clocks[node_id] = clock

    def seen(self, node_id: str, clock: int) -> bool:
        """Whether this vector has already observed up to ``clock`` from ``node_id``."""
        return self.clocks.get(node_id, 0) >= clock

    def merge(self, other: VersionVector) -> VersionVector:
        """
        Return a new VersionVector that is the pointwise (per
        node_id) maximum of ``self`` and ``other`` -- the standard
        CRDT join for grow-only version vectors. Does not mutate
        either operand.
        """
        merged: dict[str, int] = dict(self.clocks)
        for node_id, clock in other.clocks.items():
            if clock > merged.get(node_id, 0):
                merged[node_id] = clock
        return VersionVector(clocks=merged)

    def to_dict(self) -> dict[str, int]:
        """Return a plain, JSON-serialisable dict of this vector's clocks."""
        return dict(self.clocks)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> VersionVector:
        """
        Reconstruct a VersionVector from a plain dict (as produced by
        ``to_dict`` and persisted as JSON). Defensive by design: a
        missing or malformed value degrades to an empty vector rather
        than raising, mirroring
        ``cks_runtime.versioning.version_vector.VersionVector.from_metadata``.
        """
        if not isinstance(data, dict):
            return cls()
        clocks = {
            str(node_id): clock
            for node_id, clock in data.items()
            if isinstance(clock, int) and not isinstance(clock, bool)
        }
        return cls(clocks=clocks)
