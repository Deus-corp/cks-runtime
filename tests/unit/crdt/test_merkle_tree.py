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
