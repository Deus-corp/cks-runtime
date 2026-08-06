"""
CRDT adapter for distributed Knowledge Objects (ADR-013, Stage 1).

Stage 1 scope: a grow-only set (G-Set) of KnowledgeObjects
(`CRDTStore`) with a Merkle prefix tree (`MerkleTree`) for efficient
cross-node reconciliation over the existing gossip transport, plus a
per-node `VersionVector` for tracking replication progress.

Explicitly out of scope for Stage 1 (see ADR-013):
- MV-Register / fork detection (Stage 2)
- Last-Write-Wins semantics (Stage 2/3) -- this layer never removes
  or overwrites a record, only adds.
"""

from __future__ import annotations

from cks_runtime.crdt.crdt_store import (
    CRDTStore,
    InMemoryCRDTStore,
    PostgresCRDTStore,
    SQLiteCRDTStore,
    object_id_for,
)
from cks_runtime.crdt.merkle_tree import (
    EMPTY_SUBTREE_HASH,
    PostgresMerkleTree,
    SQLiteMerkleTree,
)
from cks_runtime.crdt.merkle_tree import SQLiteMerkleTree as MerkleTree
from cks_runtime.crdt.version_vector import VersionVector

__all__ = [
    "EMPTY_SUBTREE_HASH",
    "CRDTStore",
    "InMemoryCRDTStore",
    "MerkleTree",
    "PostgresCRDTStore",
    "PostgresMerkleTree",
    "SQLiteCRDTStore",
    "SQLiteMerkleTree",
    "VersionVector",
    "object_id_for",
]
