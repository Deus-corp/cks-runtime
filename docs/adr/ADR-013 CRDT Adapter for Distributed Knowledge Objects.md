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

## Stage 2: MV-Register, causality, fork detection, quarantine

Stage 1 answers "have I seen this object before" (a G-Set). Stage 2
answers the question Stage 1 explicitly deferred: "which object is the
*current* version of this logical slot" -- e.g. "what does concept X
currently say", not merely "here is everything anyone has ever said
about X". That requires a mutable pointer, and a mutable pointer
across replicas needs a real conflict story: two replicas can update
the same pointer independently, with neither aware of the other, and
that has to be detected and escalated rather than silently
overwritten.

### Decision

1. **`cks_runtime/crdt/causality.py` -- `causality_check(vv_a, vv_b)`.**
   Replaces last-write-wins for pointer updates. Returns one of
   `"dominates"` / `"dominated"` / `"concurrent"` / `"equal"` by
   comparing two `VersionVector`s pointwise per `node_id` (missing
   entries treated as clock 0). `"concurrent"` -- neither vector has
   observed the other's advances -- is the fork signal the rest of
   Stage 2 is built around. Deliberately *not* a method on
   `VersionVector` itself and not reusing ADR-007's `dominates()`: this
   needs a four-way classification (Stage 1's own docstring already
   explains why the ADR-007 type is kept separate for an unrelated
   reason -- overloading either type further would blur two already
   carefully-separated concerns).

2. **MV-Register (`cks_mv_register`)**, added to all three
   `CRDTStore` backends alongside Stage 1's `cks_knowledge_objects` /
   `cks_crdt_state`:

   ```sql
   CREATE TABLE cks_mv_register (
       pointer_key  VARCHAR(255) NOT NULL,
       object_id    VARCHAR(64)  NOT NULL,  -- G-Set object id (Stage 1)
       vector_clock JSONB/TEXT   NOT NULL,
       origin_node  VARCHAR(64)  NOT NULL,
       created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
       PRIMARY KEY (pointer_key, object_id)
   )
   ```

   The **composite primary key is the load-bearing design choice**: it
   is what lets `pointer_key` hold more than one live `object_id` at
   once (a fork), rather than forcing every write to be
   insert-or-replace the way a single-column key would. `object_id`
   here is always a Stage 1 G-Set id (`crdt_store.object_id_for`), so
   a pointer never names an object that isn't independently verifiable
   against its own Merkle leaf hash.

   `update_pointer(pointer_key, object_id, vv, origin_node)` compares
   the incoming `vv` against every existing pointer for that key via
   `causality_check`: an existing pointer the new write *dominates* is
   deleted (superseded); a pointer that *dominates* the new write
   causes the write itself to be discarded (stale replay, mirroring
   the G-Set's own "already known" no-op); anything *concurrent* is
   left standing alongside the new write -- this is the fork case, and
   is the only way `get_pointers(pointer_key)` ever returns more than
   one row. `resolve_pointer(pointer_key, winner_object_id)` is the
   sole way to collapse a fork back down to one row, and is never
   called by `update_pointer` itself -- only by a Critic Agent that has
   actually arbitrated the fork (see below).

3. **`cks_conflict_events`**, mirroring Stage 1's Merkle-tree
   design philosophy of "make divergence a first-class, queryable
   fact" rather than a side effect discovered by polling application
   state:

   ```sql
   CREATE TABLE cks_conflict_events (
       event_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       pointer_key             VARCHAR(255) NOT NULL,
       conflicting_object_ids JSONB NOT NULL,
       vector_clocks          JSONB NOT NULL,
       status                  VARCHAR(32) NOT NULL DEFAULT 'PENDING',
       created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
   )
   ```

   `escalate_fork` writes one row per detected fork and, on
   PostgreSQL, also issues `NOTIFY cks_fork_detected, '<event_id>'`.
   SQLite has no NOTIFY equivalent -- `list_pending_forks()` is the
   polling fallback, and is also what a genuinely separate
   Critic-Agent *process* uses regardless of backend, the same
   claim/poll pattern `claim_conflict_task` already uses for
   `cks_outbox_tasks` (ADR-006) rather than relying on in-process
   pub/sub a second OS process could never observe.

4. **`GossipAdapter._handle_fork` / `_detect_and_handle_fork`.**
   `_merge_crdt_objects` (Stage 1) now also calls `update_pointer` for
   every incoming object, keyed by the object's *application-level*
   identity (`identity.id`) rather than its Merkle-hash `object_id` --
   a pointer names a logical slot ("concept X"), and different
   revisions of concept X necessarily have different content hashes,
   so the G-Set id can't be the pointer key. Whenever `update_pointer`
   reports a successful (non-discarded) write, `_detect_and_handle_fork`
   re-reads `get_pointers` for that key; more than one row means
   `_handle_fork` runs: `escalate_fork` persists the row, and a new
   event, `CRDTForkDetected`, is published on the Runtime's event bus
   for any in-process subscriber -- the same escalation shape
   `GossipConflictDetected` (ADR-008) already established for
   session-level conflicts, deliberately *not* that event reused: a
   CRDT fork has no `session_id`/`source_replica_id` to speak of, only
   a `pointer_key` and the competing `object_id`s.

5. **`CRDTQuarantine` (`cks_runtime/crdt/quarantine.py`)**, adapted
   from BlackSwan's `QuarantineBuffer`. BlackSwan gated admission on a
   reputation score; CKS has no such social-trust layer, so the gate
   here is purely structural: `cks.validate()` plus confirming the
   object's claimed identity is actually derivable from its own Merkle
   leaf hash (reusing `object_id_for`, which computes an id *from* the
   object rather than trusting a separately-carried field -- so an
   object that fails to produce an id at all has already failed the
   identity check). An object failing either check is never passed to
   `CRDTStore.add_object`, so it can never enter the G-Set, be pointed
   at by an MV-Register entry, or affect the Merkle tree.

6. **`refresh_from_storage()`**, adapted from BlackSwan's
   `CRDTAdapter.refresh_from_storage`. There, it resynced an in-memory
   cache against a SQLite file a second OS process might have written
   to. CKS's `SQLiteCRDTStore`/`PostgresCRDTStore` have no such cache
   -- every read already goes straight to the connection/pool -- so
   both implementations are documented no-ops, kept only for interface
   symmetry so a caller (e.g. `GossipAdapter`) can call it
   unconditionally regardless of backend. `InMemoryCRDTStore`'s is a
   no-op for the opposite reason: there is no persistent storage, and
   no second process can ever share one Python process's memory.

### cks-mcp integration

- `cks_mcp.gossip._build_crdt_store(runtime)` constructs a
  `SQLiteCRDTStore`/`PostgresCRDTStore` wrapping the *same* connection/
  pool `runtime.storage` already holds (unwrapping `SyncStorageAdapter`
  first), so CRDT tables live in the same database as everything else.
  Returns `None` for `InMemoryStorage`, the same condition that already
  rules gossip out via `runtime.replica_id is None`.
- `setup_gossip` subscribes to `CRDTForkDetected` (mirroring the
  existing `GossipConflictDetected` subscription): each fork is
  buffered into `ConflictInbox.record_crdt_fork` for a same-process
  reader, and dual-written into the persistent outbox under task_type
  `"crdt_fork"` (payload: `pointer_key`, `conflicting_object_ids`,
  `event_id`; `session_id` is set to `pointer_key`, since a CRDT fork
  has no session) for an out-of-process Critic Agent worker to claim
  via the existing `claim_conflict_task` tool.
- `critic_agent.py` adds `"crdt_fork"` to `_TASK_TYPES` / `_RESOLVERS`.
  `resolve_crdt_fork` is deliberately mechanical (no LLM), the same
  "safe default policy" philosophy as `resolve_temporal_conflict`/
  `resolve_contradiction_conflict`: since every competing `object_id`
  is a content hash every replica computes identically, picking the
  lexicographically-last id as the winner is an arbitrary but
  *replica-agnostic* deterministic tie-break -- every Critic Agent
  across every replica converges on the same winner independently,
  with no coordination needed, unlike "keep whichever this replica saw
  first". This is explicitly a placeholder policy, not a claim that
  lexicographic order is semantically meaningful; a future
  content-aware arbitration (`construct_knowledge`/
  `arbitrate_inference_conflict`-based) can replace it without
  changing the task/payload shape.

## Explicitly out of scope (Stage 2)

- **Semantic/content-aware fork arbitration** -- `resolve_crdt_fork`'s
  lexicographic tie-break is a deterministic placeholder, not an
  attempt to judge which side is "right". Teaching the Critic Agent to
  inspect both objects' actual content before choosing is future work.
- **Postgres `LISTEN`-based consumption** -- `escalate_fork` sends
  `NOTIFY` on PostgreSQL, but cks-mcp does not yet run a `LISTEN`
  loop to react to it instantly; `list_pending_forks`/the outbox
  dual-write are the only consumption paths wired up so far. The
  `NOTIFY` is emitted now so a future listener can be added without
  touching `crdt_store.py` again.
- Changes to `SQLiteStorage`/`PostgresStorage` -- unchanged from Stage
  1's constraint; MV-Register/conflict-event tables live entirely in
  `CRDTStore`'s own schema.

## Consequences (Stage 2)

- `update_pointer` is called once per object per gossip round (same
  frequency as Stage 1's `add_object`), each a small read (existing
  pointers for that key -- normally 0 or 1 rows, 2+ only mid-fork)
  plus at most one insert and a handful of deletes. Cheap relative to
  the G-Set insert it accompanies.
- A pointer that has forked stays forked -- consuming zero, one, or
  many concurrent rows via `get_pointers` -- until a Critic Agent (or
  direct `resolve_pointer` call) collapses it. Nothing else in this
  layer resolves a fork on its own; that is intentional, matching how
  `GossipConflictDetected`/`InferenceConflictDetected` are never
  silently auto-resolved by the runtime that raises them either.