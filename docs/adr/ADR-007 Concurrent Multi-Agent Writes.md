# ADR-007

# Concurrent Multi-Agent Writes: Operation Log and Version Vectors

**Status:** Proposed

**Date:** 2026-07-25

**Category:** Architecture Decision Record

---

# Context

As of v1.15.0, `SQLiteStorage` provides two independent mechanisms for
concurrent writers:

- `PRAGMA busy_timeout` plus `_retry_on_locked`, which absorbs
  transient `sqlite3.OperationalError: database is locked` failures
  under multi-process contention.
- Optimistic concurrency control (`expected_version_id` CAS) on
  `save_session`, which rejects a commit outright via
  `ConcurrentModificationError` when another writer has already
  advanced the session past the version this writer last observed.

Neither mechanism addresses semantic conflicts between two branches
that both changed the same `object_id`. That is handled separately,
by `MergeOperation` / `CoreBridge.merge()`, which raises
`RuntimeMergeConflictError` (`cks_runtime.core_api.merge_conflict`)
whenever base, branch_a and branch_b disagree on an identity.

Today that conflict check operates at **whole-object granularity**.
`cks-mcp`'s `merge_branch` tool already computes a **field-level**
diff for the conflict it reports back to the calling agent
(`_generate_diff` in `cks_mcp/tools/merge.py` walks `structure` keys),
but that diff is presentation only — the actual resolution still
replaces the whole object (`"branch_a"` / `"branch_b"` / a supplied
replacement), never a per-field merge of non-overlapping changes.

As more LLM agents edit the same Knowledge Structure concurrently,
this produces two costs an OCC/retry layer doesn't solve:

1. Two agents that changed **different fields of the same object**
   still get a conflict report and must resolve it by hand, even
   though nothing they wrote actually contradicts the other.
2. `merge_branch` has no way to know that one branch already contains
   everything the other branch has (a fast-forward case) — it always
   performs a full three-way diff against the recorded base.

---

# Constraint carried over from ADR-005

ADR-005 (Version History) already rejected an event-only Runtime:

> Event sourcing may be implemented internally, but observable
> Runtime behaviour shall remain Version-based.

Both proposals below are scoped to satisfy that constraint: they are
internal aids to conflict resolution, not a replacement for the
existing immutable, append-only `RuntimeVersion` history. A caller
that never touches the new APIs sees no behavioural difference.

They must also respect ADR-004 (Storage Abstraction): neither
mechanism may leak into `Runtime`/`ExecutionPipeline` in a way that
assumes a SQLite-specific representation. Both are opt-in, with a
no-op default on the `RuntimeStorage` base class — the same pattern
already used for `enqueue_task`, `save_object_embeddings`, etc.

---

# Decision (proposed)

## 1. Operation log (`cks_operation_log`)

A new, storage-owned, append-only table recording individual
structural operators as they're applied, in addition to (not instead
of) the existing `versions` table:

```sql
CREATE TABLE IF NOT EXISTS cks_operation_log (
    op_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    version_id  TEXT NOT NULL,   -- version this op was committed as part of
    object_id   TEXT NOT NULL,   -- target KnowledgeObject/CanonicalRelation id
    op_type     TEXT NOT NULL,   -- 'set_field' | 'add_object' | 'add_relation' | 'remove_object' | 'remove_relation'
    field_key   TEXT,            -- populated only for 'set_field'
    field_value TEXT,            -- JSON-encoded new value, only for 'set_field'
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_op_log_object ON cks_operation_log(session_id, object_id);
```

Rows are never updated or deleted — the same immutability rule
`RuntimeVersion` already follows.

**Where it's populated:** `ExecutionPipeline._persist`, right next to
the existing `save_version` call, from the same `patch` the version
is already built from. No new call site in `Runtime` or the
executor is needed.

**How `MergeOperation` would use it (fast path, before falling back to
today's whole-object check):** for each `object_id` both branches
touched relative to the base, look up the `cks_operation_log` rows
each branch produced for that object since the merge base's
`version_id`. If the two branches' rows touch **disjoint sets of
`field_key`s**, synthesize the merged object by applying both sets of
`set_field` ops on top of the base object and skip the conflict
entirely. If they touch overlapping keys, or either side used a
non-`set_field` op (e.g. one branch removed the object), fall back to
`RuntimeMergeConflictError` exactly as today — this is strictly
additive, never a behavior change for the cases already handled.

This turns "two agents edited the same object" conflicts into
"two agents edited the same *field*" conflicts, which is the
actually-contested case.

## 2. Version vectors (`VersionVector`)

```python
@dataclass(slots=True)
class VersionVector:
    clocks: dict[str, int] = field(default_factory=dict)  # node_id -> highest op counter seen from that node

    def bump(self, node_id: str) -> None:
        self.clocks[node_id] = self.clocks.get(node_id, 0) + 1

    def observe(self, node_id: str, clock: int) -> None:
        self.clocks[node_id] = max(self.clocks.get(node_id, 0), clock)

    def dominates(self, other: "VersionVector") -> bool:
        """True if this vector has seen everything `other` has."""
        return all(v <= self.clocks.get(k, 0) for k, v in other.clocks.items())
```

**Where it's stored:** initially as a plain dict under
`RuntimeSession.metadata["version_vector"]` rather than a new
dataclass field — `RuntimeSession` is a `slots=True` dataclass
persisted as opaque JSON by every backend already (see
`save_session`'s `data["metadata"]`), so this needs no migration and
no change to `RuntimeStorage`'s interface. It can graduate to a
proper field later if it proves durable.

**When it updates:** on each committed version, `VersionManager`
bumps the vector for the local `node_id` (an agent/process identifier
— not yet a Runtime concept; would need one, e.g. a
`runtime_id`/`agent_id` passed at `Runtime` construction, defaulting
to a random id so existing single-writer callers are unaffected).

**How `merge_branch` would use it, guarded by an opt-in
`use_vector_clocks=True`:** before running the three-way merge at
all, compare `target.version_vector` and `source.version_vector`. If
`target` already dominates `source`, the merge is a no-op (source has
nothing target hasn't seen). If `source` dominates `target`, the
merge is a fast-forward: adopt `source`'s structure directly, no
diff, no conflict check. Only when neither dominates the other does
the existing (or field-aware, per §1) three-way merge run.

---

# Non-goals

- Replacing `RuntimeMergeConflictError`/the existing whole-object
  merge path — both proposals are a fast path in front of it, not a
  new conflict model callers must learn.
- Requiring every `RuntimeStorage` backend to implement this from
  day one. `InMemoryStorage` and any third-party backend keep working
  via the same no-op-by-default pattern `supports_outbox` already
  established; only backends that opt in get the fast paths.
- Solving conflicting edits to the *same field* — that remains a
  genuine conflict and still surfaces via `RuntimeMergeConflictError`.

---

# Consequences

## Positive

- Field-disjoint concurrent edits from different agents merge
  automatically instead of round-tripping through an LLM agent for
  manual resolution.
- Fast-forward merges skip a three-way diff entirely when one branch
  is already known to contain the other.
- Both are additive and backend-optional; no existing caller,
  including every current test, changes behavior.

## Negative

- `cks_operation_log` grows unboundedly on a long-lived session,
  same as `versions` already does — needs the same eventual
  retention/compaction story, not solved here.
- Introduces a `node_id`/`agent_id` concept Runtime doesn't have
  today; needs its own small design pass before `VersionVector` can
  actually bump anything.
- Two independent merge fast-paths (field-disjoint ops, vector
  dominance) add branches to `MergeOperation` that need their own
  test coverage and are easy to get subtly wrong around deletes.

---

# Alternatives considered

## Whole-session locking during an agent's editing turn

Rejected. Serializes every concurrent agent against a shared session,
which is the concurrency problem this ADR exists to avoid, not a
solution to it.

## Full CRDT field types (e.g. LWW-registers, OR-sets) per Core field

Rejected for now. `cks-core`'s `KnowledgeObject.structure` is
implementation-defined per Core, and imposing a specific CRDT
representation on every field would cross the Runtime/Core boundary
ADR-001 already draws. The operation-log approach in §1 gets most of
the practical benefit (disjoint-field auto-merge) without requiring
Core to adopt CRDT field semantics.

---

# Status

Proposed. Not yet implemented. Filed here per the request to record
the design before committing to the schema or the `node_id` concept
it depends on; both need a follow-up ADR update once `node_id`
ownership is settled.
