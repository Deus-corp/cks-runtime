"""
Version Vectors (ADR-007 Part 2: Concurrent Multi-Agent Writes).

A VersionVector tracks, per node_id, the highest local commit clock
that a RuntimeSession's current Knowledge Structure is known to
reflect. It backs two MergeOperation fast paths ahead of the existing
three-way merge:

- both branches unchanged relative to each other (target already
  dominates source) -> no-op;
- one branch is a strict descendant of the other (source dominates
  target) -> fast-forward, no diff, no conflict check.

Only when neither dominates the other does the existing (already
field-aware, per ADR-007 Part 1) three-way merge run. See the ADR
for the full design and the alternatives considered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Key under which a session's VersionVector is stored in
#: ``RuntimeSession.metadata`` -- chosen over a dedicated
#: RuntimeSession field so this needs no storage schema change:
#: ``metadata`` is already a plain ``dict[str, Any]`` persisted as
#: opaque JSON by every RuntimeStorage backend (ADR-007).
VERSION_VECTOR_KEY = "version_vector"


@dataclass(slots=True)
class VersionVector:
    """
    Per-node commit clocks for a single RuntimeSession.

    ``clocks`` maps ``node_id -> highest commit clock observed from
    that node``. A vector with no entries dominates nothing and is
    dominated by anything with at least one entry -- i.e. a session
    that has never committed under this scheme carries no
    information either way, so both proposed fast paths simply fall
    through to the existing three-way merge for it (see ``dominates``
    below and ``MergeOperation.execute``).
    """

    clocks: dict[str, int] = field(default_factory=dict)

    def bump(self, node_id: str) -> None:
        """Record one more local commit from ``node_id``."""
        self.clocks[node_id] = self.clocks.get(node_id, 0) + 1

    def observe(self, node_id: str, clock: int) -> None:
        """Record having seen up to ``clock`` from ``node_id``."""
        self.clocks[node_id] = max(self.clocks.get(node_id, 0), clock)

    def absorb(self, other: VersionVector) -> None:
        """
        Fold every entry of ``other`` into this vector via ``observe``.

        Called after a successful merge_branch (fast-forward, no-op,
        or a genuine three-way merge) so the target session's vector
        keeps meaning "everything this session's current state
        reflects" -- without this, a target that just fast-forwarded
        to source's structure would still compare as if it hadn't,
        and a later merge could re-run a full three-way diff (or
        mis-detect dominance) against a branch it already fully
        incorporated.
        """
        for node_id, clock in other.clocks.items():
            self.observe(node_id, clock)

    def dominates(self, other: VersionVector) -> bool:
        """
        Whether this vector has seen at least as much as ``other``
        from every node ``other`` knows about.

        An empty ``self`` never dominates anything, including an
        equally empty ``other`` -- it is never treated as "ahead" of
        any vector, empty or not. Two empty vectors therefore do NOT
        dominate each other, so that the existing three-way merge
        always runs for sessions that have no version-vector history
        yet (see ``MergeOperation.execute``, which relies on exactly
        this to decide when the no-op/fast-forward paths apply).
        """
        if not self.clocks:
            return False
        return all(clock <= self.clocks.get(node_id, 0) for node_id, clock in other.clocks.items())

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> VersionVector:
        """
        Reconstruct a VersionVector from a RuntimeSession's (or
        RuntimeVersion's) ``metadata`` mapping.

        Defensive by design: ``metadata`` is caller-supplied opaque
        JSON (any adapter or a hand-built session could put anything
        under this key), so a missing or malformed value degrades to
        an empty vector rather than raising -- a corrupt or foreign
        ``version_vector`` entry should cost a session its fast-path
        eligibility, not its ability to commit or merge at all.
        Booleans are explicitly excluded even though ``bool`` is a
        subclass of ``int`` in Python -- a stray ``true``/``false``
        under a clock key should not silently become ``1``/``0``.
        """
        raw = metadata.get(VERSION_VECTOR_KEY)
        if not isinstance(raw, dict):
            return cls()
        clocks = {
            str(node_id): clock
            for node_id, clock in raw.items()
            if isinstance(clock, int) and not isinstance(clock, bool)
        }
        return cls(clocks=clocks)

    def to_metadata(self, metadata: dict[str, Any]) -> None:
        """Write this vector into ``metadata`` under its well-known key."""
        metadata[VERSION_VECTOR_KEY] = dict(self.clocks)