# ADR-013

# CRDT Adapter for Distributed Knowledge Objects: G-Set + Merkle Tree (Stage 1)

**Status:** Implemented (Stage 1 of 3)

**Date:** 2026-08-06

**Category:** Architecture Decision Record

---

## Context

ADR-008's `GossipAdapter` reconciles `RuntimeSession` snapshots between
replicas by reusing the existing ADR-007 three-way merge
(`MergeOperation`). That works well for a session's *structure*
(relations, field-level conflicts), but it has two properties that
motivate this ADR:

1. When the session-level merge finds a genuine conflict, the merge is
   *not* applied -- the conflict is published as `GossipConflictDetected`
   and left for a Critic agent (or a human) to resolve. Any
   `KnowledgeObject` that only appears in the losing/conflicting side is,
   until that resolution happens, not durably recorded anywhere on this
   replica.
2. Reconciling two replicas' full knowledge (rather than one shared
   session) still costs an O(n) structural diff per gossip round, with no
   cheaper way to first check whether two replicas already agree.

We want a second, independent layer underneath the session merge: a
plain grow-only set of every `KnowledgeObject` any replica has ever
produced, with no possibility of write-write conflict (since it only
ever adds), and a way to compare two replicas' sets cheaply before
doing any real work.

## Decision

Add a `cks_runtime/crdt/` module implementing:

1. **`CRDTStore`** -- a G-Set (grow-only set) of `KnowledgeObject`s,
   keyed by the object's own SHA-256 leaf hash (`KnowledgeObject._hash`,
   hex-encoded), not by its application-level `ObjectIdentity.id`.
   Content-addressing is what makes "insert if id absent" a correct,
   order-independent, conflict-free merge: two replicas that
   independently produce bit-identical objects converge on one record
   automatically, and there is no way for a re-delivered gossip message
   to double-count. Backed by `SQLiteCRDTStore`, `PostgresCRDTStore`
   (async), and `InMemoryCRDTStore` (tests) -- deliberately separate
   tables (`cks_knowledge_objects`, `cks_crdt_state`) from the existing
   `sessions`/`versions` tables, so `SQLiteStorage`/`PostgresStorage`
   need no changes.

2. **`MerkleTree`** -- a radix-16 prefix tree over the 64-hex-character
   object ids. Level 64 nodes are the leaves (ids themselves, already
   content hashes); each level `L < 64` node hashes its (up to) 16
   children at level `L+1`, with a well-known `EMPTY_SUBTREE_HASH`
   standing in for absent children. Inserting one object touches exactly
   the 65 nodes on its root-to-leaf path (`update_merkle_path`) -- O(1)
   in the number of objects already stored, not O(n). Two replicas can
   compare root hashes first (`get_root_hash`); on a mismatch, walk down
   only the differing branches via `get_children_hashes(prefix)` instead
   of diffing every object.

   SQLite has no stored procedures, so `SQLiteMerkleTree` recomputes the
   path in Python on every insert. PostgreSQL gets the same computation
   twice, deliberately: a PL/pgSQL trigger
   (`update_merkle_tree_on_insert`, attached to `cks_knowledge_objects`)
   keeps the tree correct for *any* client that inserts a row, including
   ones that bypass this Python layer entirely; `PostgresMerkleTree`'s
   own Python methods use the identical algorithm so the two paths
   always agree, and serve as a fallback for a table created without the
   trigger installed (e.g. a database without the `pgcrypto` extension
   available at the time `ensure_schema` ran).

3. **`VersionVector`** (`cks_runtime/crdt/version_vector.py`) -- a
   small, separate type from `cks_runtime.versioning.version_vector.
   VersionVector` (ADR-007). The ADR-007 vector is anchored to
   `RuntimeSession.metadata` and its `dominates`/`absorb` API exists
   specifically to drive `MergeOperation`'s fast-forward/no-op decision.
   The CRDT layer's vector instead tracks, per `node_id`, how many CRDT
   records that node has locally produced, persisted in
   `cks_crdt_state`, and only ever needs a symmetric `merge` (pointwise
   max) plus `seen`/`observe` -- there is no "does A dominate B" question
   to answer for a G-Set, since merging two G-Sets is always safe
   regardless of either side's vector. Reusing the ADR-007 type would
   have overloaded it with a second, unrelated persistence contract, so
   a new type was created instead of adapting the existing one in place.

4. **Gossip integration** -- `GossipAdapter` takes an optional
   `crdt_store` constructor argument (`None` by default; existing
   callers are unaffected). `_apply_remote_session_locked` now calls
   `_merge_crdt_objects(remote_session)` *before* any session-level
   dominance/fast-forward/merge decision, unconditionally adding every
   object in the remote snapshot into the local G-Set. This guarantees
   the G-Set reflects everything this replica has ever observed, even
   for a remote session whose session-level reconciliation ends in an
   unresolved conflict.

## Explicitly out of scope (Stage 1)

- **MV-Register / fork detection** -- deferred to Stage 2. Stage 1's
  G-Set has no notion of "the same logical slot with two competing
  values"; every distinct `(identity, structure)` pair is just another
  set member.
- **Last-Write-Wins** -- deferred to Stage 2/3. This layer never removes
  or overwrites a record once added.
- Changes to `SQLiteStorage`/`PostgresStorage` -- the CRDT adapter owns
  its own tables and never touches `sessions`/`versions`.

## Consequences

- Every gossip round now does one extra pass over the incoming
  session's objects (`O(objects in that session)`), each a cheap
  "insert if absent" plus, for genuinely new objects, 65 Merkle-node
  upserts. This is strictly additive to the existing session merge cost
  and does not change its control flow or outcome.
- A future Stage 2 (MV-Register/fork detection) can build directly on
  `CRDTStore.list_objects()`/the Merkle tree's divergence-localization
  without touching Stage 1's schema, since G-Set membership is monotonic
  and Stage 2 only needs to add resolution semantics on top, not change
  what's stored.
- `get_root_hash()`/`get_children_hashes()` are ready for a future
  gossip-transport addition that exchanges root hashes before whole
  session snapshots, letting two already-converged replicas skip a
  round entirely -- not implemented in Stage 1, since `GossipAdapter`'s
  existing `structurally_equivalent` check already provides an
  equivalent short-circuit at the session level.
