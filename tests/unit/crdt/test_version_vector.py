from __future__ import annotations

from cks_runtime.crdt.version_vector import VersionVector


def test_bump_starts_at_one_and_increments():
    vv = VersionVector()
    assert vv.bump("node-a") == 1
    assert vv.bump("node-a") == 2
    assert vv.clocks == {"node-a": 2}


def test_observe_only_moves_forward():
    vv = VersionVector()
    vv.observe("node-a", 5)
    assert vv.clocks["node-a"] == 5
    vv.observe("node-a", 3)
    assert vv.clocks["node-a"] == 5
    vv.observe("node-a", 7)
    assert vv.clocks["node-a"] == 7


def test_seen():
    vv = VersionVector(clocks={"node-a": 5})
    assert vv.seen("node-a", 3) is True
    assert vv.seen("node-a", 5) is True
    assert vv.seen("node-a", 6) is False
    assert vv.seen("node-b", 0) is True
    assert vv.seen("node-b", 1) is False


def test_merge_is_pointwise_max_and_non_mutating():
    a = VersionVector(clocks={"n1": 3, "n2": 1})
    b = VersionVector(clocks={"n2": 5, "n3": 2})
    merged = a.merge(b)
    assert merged.clocks == {"n1": 3, "n2": 5, "n3": 2}
    # originals untouched
    assert a.clocks == {"n1": 3, "n2": 1}
    assert b.clocks == {"n2": 5, "n3": 2}


def test_merge_is_commutative():
    a = VersionVector(clocks={"n1": 3, "n2": 1})
    b = VersionVector(clocks={"n2": 5, "n3": 2})
    assert a.merge(b).clocks == b.merge(a).clocks


def test_to_dict_from_dict_roundtrip():
    vv = VersionVector(clocks={"node-a": 4, "node-b": 9})
    data = vv.to_dict()
    restored = VersionVector.from_dict(data)
    assert restored.clocks == vv.clocks
    # to_dict returns a copy, not a live view
    data["node-a"] = 999
    assert vv.clocks["node-a"] == 4


def test_from_dict_defensive_against_malformed_input():
    assert VersionVector.from_dict(None).clocks == {}
    assert VersionVector.from_dict({}).clocks == {}
    assert VersionVector.from_dict({"a": "not-an-int"}).clocks == {}
    assert VersionVector.from_dict({"a": True}).clocks == {}
    assert VersionVector.from_dict({"a": 3, "b": "x"}).clocks == {"a": 3}
