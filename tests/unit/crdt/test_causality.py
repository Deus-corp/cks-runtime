from __future__ import annotations

from cks_runtime.crdt.causality import (
    CONCURRENT,
    DOMINATED,
    DOMINATES,
    EQUAL,
    causality_check,
)
from cks_runtime.crdt.version_vector import VersionVector


def test_dominates_when_strictly_ahead_on_all_shared_nodes():
    a = VersionVector(clocks={"n1": 3, "n2": 2})
    b = VersionVector(clocks={"n1": 1, "n2": 1})
    assert causality_check(a, b) == DOMINATES


def test_dominated_is_the_mirror_of_dominates():
    a = VersionVector(clocks={"n1": 1, "n2": 1})
    b = VersionVector(clocks={"n1": 3, "n2": 2})
    assert causality_check(a, b) == DOMINATED


def test_equal_when_clocks_identical():
    a = VersionVector(clocks={"n1": 2, "n2": 5})
    b = VersionVector(clocks={"n1": 2, "n2": 5})
    assert causality_check(a, b) == EQUAL


def test_equal_when_both_empty():
    assert causality_check(VersionVector(), VersionVector()) == EQUAL


def test_concurrent_when_each_ahead_on_different_node():
    a = VersionVector(clocks={"n1": 3, "n2": 1})
    b = VersionVector(clocks={"n1": 1, "n2": 3})
    assert causality_check(a, b) == CONCURRENT


def test_concurrent_when_one_side_has_a_node_the_other_never_saw():
    a = VersionVector(clocks={"n1": 1})
    b = VersionVector(clocks={"n2": 1})
    assert causality_check(a, b) == CONCURRENT


def test_dominates_with_a_new_node_not_present_in_b():
    a = VersionVector(clocks={"n1": 1, "n2": 1})
    b = VersionVector(clocks={"n1": 1})
    assert causality_check(a, b) == DOMINATES


def test_does_not_mutate_inputs():
    a = VersionVector(clocks={"n1": 3})
    b = VersionVector(clocks={"n1": 1})
    a_before, b_before = dict(a.clocks), dict(b.clocks)
    causality_check(a, b)
    assert a.clocks == a_before
    assert b.clocks == b_before