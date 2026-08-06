"""
Causality comparison between two ``VersionVector``s (ADR-013, Stage 2).

Replaces last-write-wins (LWW) for MV-Register pointer updates: instead
of comparing wall-clock timestamps (which are not reliable across
independently-clocked nodes and silently drop concurrent writes), the
MV-Register compares the *vector clocks* attached to each pointer
update and asks a strictly causal question -- did one update already
know about the other when it was made, or did they happen
independently (a fork)?

This mirrors ``cks_runtime.versioning.version_vector.VersionVector``'s
own ``dominates()`` (used for the ADR-007 session merge fast-forward
decision), but is written against the *separate*
``cks_runtime.crdt.version_vector.VersionVector`` type (ADR-013's own,
persisted in ``cks_crdt_state``/``cks_mv_register``) and returns a
four-way classification instead of a boolean, since ``update_pointer``
needs to distinguish "strictly newer", "strictly older", "identical",
and "concurrent" (fork) rather than just "does this dominate".
"""

from __future__ import annotations

from cks_runtime.crdt.version_vector import VersionVector

#: The four possible outcomes of comparing two VersionVectors.
CausalityResult = str

DOMINATES: CausalityResult = "dominates"
DOMINATED: CausalityResult = "dominated"
CONCURRENT: CausalityResult = "concurrent"
EQUAL: CausalityResult = "equal"


def causality_check(vv_a: VersionVector, vv_b: VersionVector) -> CausalityResult:
    """
    Compare two vector clocks and classify their causal relationship.

    - ``"dominates"``: ``vv_a`` has seen everything ``vv_b`` has seen
      (and at least one clock strictly higher) -- ``vv_a`` is causally
      newer.
    - ``"dominated"``: the mirror image -- ``vv_b`` is causally newer.
    - ``"equal"``: both vectors carry identical clocks for every
      node_id that appears in either (missing entries are treated as
      clock 0, same as ``VersionVector.clocks.get(node_id, 0)``
      elsewhere in this package).
    - ``"concurrent"``: neither vector has observed the other's
      updates -- e.g. ``vv_a`` is ahead on node X while ``vv_b`` is
      ahead on node Y. This is the fork case: two updates were made
      independently, with neither aware of the other, and both must be
      kept until a Critic agent resolves which one wins (or that they
      should be merged).

    Pure and side-effect-free: does not mutate either input.
    """
    node_ids = set(vv_a.clocks) | set(vv_b.clocks)

    a_has_greater = False
    b_has_greater = False

    for node_id in node_ids:
        a_clock = vv_a.clocks.get(node_id, 0)
        b_clock = vv_b.clocks.get(node_id, 0)
        if a_clock > b_clock:
            a_has_greater = True
        elif b_clock > a_clock:
            b_has_greater = True

    if a_has_greater and b_has_greater:
        return CONCURRENT
    if a_has_greater:
        return DOMINATES
    if b_has_greater:
        return DOMINATED
    return EQUAL