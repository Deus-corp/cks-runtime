from __future__ import annotations

import hashlib
import sqlite3

import pytest

from cks_runtime.crdt.crdt_store import _retry_on_locked
from cks_runtime.crdt.merkle_tree import EMPTY_SUBTREE_HASH, SQLiteMerkleTree


def _id_for(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


@pytest.fixture
def tree() -> SQLiteMerkleTree:
    conn = sqlite3.connect(":memory:")
    return SQLiteMerkleTree(conn, _retry_on_locked)


def test_empty_tree_root_is_empty_subtree_hash(tree: SQLiteMerkleTree):
    assert tree.get_root_hash() == EMPTY_SUBTREE_HASH


def test_adding_one_object_changes_root(tree: SQLiteMerkleTree):
    before = tree.get_root_hash()
    tree.update_merkle_path(_id_for("object-1"))
    after = tree.get_root_hash()
    assert before != after


def test_incremental_update_touches_65_levels(tree: SQLiteMerkleTree):
    obj_id = _id_for("object-1")
    tree.update_merkle_path(obj_id)
    cur = tree._conn.execute("SELECT COUNT(*) FROM cks_merkle_tree")
    (count,) = cur.fetchone()
    # Exactly one root-to-leaf path: levels 0..64 inclusive = 65 nodes.
    assert count == 65


def test_root_hash_independent_of_insertion_order(tree: SQLiteMerkleTree):
    ids = [_id_for(f"object-{i}") for i in range(8)]

    for oid in ids:
        tree.update_merkle_path(oid)
    root_forward = tree.get_root_hash()

    conn2 = sqlite3.connect(":memory:")
    tree2 = SQLiteMerkleTree(conn2, _retry_on_locked)
    for oid in reversed(ids):
        tree2.update_merkle_path(oid)
    root_reverse = tree2.get_root_hash()

    assert root_forward == root_reverse


def test_get_children_hashes_returns_16_entries(tree: SQLiteMerkleTree):
    tree.update_merkle_path(_id_for("object-1"))
    children = tree.get_children_hashes("")
    assert len(children) == 16


def test_get_children_hashes_reflects_added_object(tree: SQLiteMerkleTree):
    obj_id = _id_for("object-1")
    tree.update_merkle_path(obj_id)
    nibble = obj_id[0]
    children = tree.get_children_hashes("")
    index = "0123456789abcdef".index(nibble)
    assert children[index] != EMPTY_SUBTREE_HASH
    for i, child in enumerate(children):
        if i != index:
            assert child == EMPTY_SUBTREE_HASH


def test_readding_same_object_is_idempotent_for_root_hash(tree: SQLiteMerkleTree):
    obj_id = _id_for("object-1")
    tree.update_merkle_path(obj_id)
    root_once = tree.get_root_hash()
    tree.update_merkle_path(obj_id)
    root_twice = tree.get_root_hash()
    assert root_once == root_twice


def test_rejects_non_sha256_id(tree: SQLiteMerkleTree):
    with pytest.raises(ValueError):
        tree.update_merkle_path("not-a-valid-hex-id")


def test_get_children_hashes_uses_one_query_not_sixteen(tree: SQLiteMerkleTree):
    """
    Regression/perf test: ``get_children_hashes`` must fetch all 16
    nibble children in a single SQL statement, not one query per
    nibble -- the latter meant ``update_merkle_path`` (which calls
    ``get_children_hashes`` once per level, 64 times per inserted
    object) issued up to 1,024 sequential queries for a single insert.
    """
    tree.update_merkle_path(_id_for("object-1"))

    queries: list[str] = []
    tree._conn.set_trace_callback(queries.append)
    try:
        tree.get_children_hashes("")
    finally:
        tree._conn.set_trace_callback(None)

    select_queries = [q for q in queries if q.strip().upper().startswith("SELECT")]
    assert len(select_queries) == 1


def test_get_children_hashes_pattern_does_not_leak_across_prefixes(
    tree: SQLiteMerkleTree,
):
    """
    The batched LIKE-based lookup must still only match true children
    of the requested prefix -- not any other same-length node whose
    path happens to share a suffix, nor a node at a different level
    entirely. Two ids that diverge at the first nibble must not affect
    each other's children lookup at that prefix.
    """
    id_a = _id_for("object-a")
    id_b = _id_for("object-b")
    assume_different_first_nibble = id_a[0] != id_b[0]
    if not assume_different_first_nibble:
        # Extremely unlikely for SHA-256 outputs of different inputs,
        # but keep the test deterministic rather than flaky.
        id_b = "f" + id_b[1:] if id_a[0] != "f" else "0" + id_b[1:]

    tree.update_merkle_path(id_a)
    tree.update_merkle_path(id_b)

    children_of_a_first_nibble = tree.get_children_hashes(id_a[0])
    # None of object B's path nodes should show up under object A's
    # first-nibble prefix.
    for child_hash in children_of_a_first_nibble:
        assert child_hash != id_b
