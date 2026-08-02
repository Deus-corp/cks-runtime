# ADR-008

# Gossip Replication for Distributed Runtime Nodes: Persistent Replica Identity and Operation-Log Exchange

**Status:** Partially Implemented

**Date:** 2026-08-01

**Category:** Architecture Decision Record

---

**Status (2026-08-01):** Storage-layer portion implemented in
`cks-runtime` v1.26.0: `RuntimeStorage.get_or_create_replica_id`,
`RuntimeStorage.fetch_operations_since`, and the
`GossipConflictDetected` event exist and are wired through `SQLiteStorage`,
`InMemoryStorage`, and `PostgresStorage`.

**Revision (2026-08-01, v1.26.1):** The Decision section below (point
2, "Fetch on gap" / "Apply through the existing fast path") described
reconstructing a remote Knowledge Structure by replaying raw
`RuntimeFieldOperation` rows. That cannot work as specified: per
`RuntimeFieldOperation`'s own contract, an `"add_object"`/
`"add_relation"` entry carries no payload at all -- it only marks that
an identity appeared, so a genuinely new object can never be
reconstructed from the operation log alone. `GossipAdapter` was
rewritten around this: it exchanges whole `RuntimeSession` snapshots
(which already carry a complete `knowledge_structure`) for a session
both replicas track, and reconciles them through the same two-phase
probe-then-commit sequence `cks-mcp`'s `merge_branch` tool already
uses -- `executor.execute(MergeOperation(...))` to detect a conflict
with no persisted side effects, then, only on success,
`begin_transaction`/`commit_transaction`. This is the existing
ADR-007 merge mechanism, reused, not a new one; see the module
docstring in `cks_runtime/gossip/adapter.py` for the full rationale.
`fetch_operations_since`/`get_or_create_replica_id` remain useful as a
transport-layer accelerant and durable peer identity, but are no
longer the payload the merge itself is built from. All 12
`tests/unit/gossip/test_gossip_adapter.py` cases now pass (none
skipped); `mypy`/`ruff` are clean across the package. The gossip
transport itself (peer discovery, scheduling, wire format) remains
unimplemented -- see Non-Goals.

**Revision (2026-08-01, v1.29.0):** Point 1's "every session created
by that process bumps its VersionVector under `replica_id` in
addition to ... `node_id`" is now implemented: `Runtime.create()`
sources `replica_id` from `storage.get_or_create_replica_id()` once
at startup (`Runtime.replica_id`, `None` for a bare `Runtime(...)`
or a storage backend without gossip support), and
`ExecutionPipeline._create_version` passes it through to
`VersionManager.create(..., replica_id=...)`, which bumps the vector
for it alongside `node_id`. This closes Problem 1 as stated --
`replica_id` now survives a process restart the way `node_id` never
could. It does **not**, on its own, make concurrently-bootstrapped
replicas of the same `session_id` converge: `apply_remote_session`'s
non-fast-forward path still goes through `MergeOperation`, which
needs `parent_version_id` lineage to compute a three-way merge base.
Sessions independently constructed on separate replicas (rather than
via `create_branch`) have no such lineage, so once both sides have
committed anything, every further gossip round between them fails
with "could not determine a merge base" and escalates via
`GossipConflictDetected` -- indefinitely, not just once. Confirmed
against a 3-replica local reproduction (Supervisor/Critic/Worker,
one field-disjoint commit each): 20+ anti-entropy rounds, zero
transport failures, zero convergence. Establishing shared lineage
for gossip-bootstrapped sessions is unaddressed and is the next gap
to close, not this revision's scope.

**Revision (2026-08-02, v1.30.0):** The lineage gap above is closed
-- `EMPTY_STATE_VERSION_ID` (`cks_runtime/operations/operation_types.py`,
`"00000000-0000-0000-0000-000000000000"`), the same trick as git's
empty-tree hash. Two sessions whose `parent_version_id` both equal
this constant are defined to share it as a common ancestor without
either ever having seen the other's real `version_history`:
`MergeOperation.execute` resolves it to an empty structure directly,
skipping the `get_version_state()` lookup that requires the id to
physically appear in local history. `_bootstrap_remote_session` now
always anchors the newly-adopted local copy to it (regardless of
what the remote's own `parent_version_id` was -- that pointer lives
in the remote's history, which this replica never receives and never
will, gossip carrying snapshots, not history). `GossipAdapter
.anchor_genesis(session)` does the equivalent for a session's true
origin -- the one replica in a deployment whose session was created
locally via `Runtime.create_session()`, not received via gossip, and
so needs one explicit call right after creation to get the same
anchor every bootstrap joiner gets automatically. Re-ran the same
3-replica reproduction from the prior revision with these three
pieces in place: converges within a handful of rounds, zero
escalated conflicts (`tests/unit/gossip/test_gossip_adapter.py::TestThreeReplicaConvergenceViaGenesis`).
`EMPTY_STATE_VERSION_ID` is opt-in -- sessions that never call
`anchor_genesis()`/never get bootstrapped keep today's `None` default
and today's escalate-on-divergence behavior unchanged.

---

# Context

ADR-007 (Concurrent Multi-Agent Writes) is implemented, not just
proposed: `cks_operation_log` and `VersionVector` both exist and are
wired through `ExecutionPipeline._persist`, `VersionManager`, and
`MergeOperation`'s fast paths, on both `SQLiteStorage` and
`PostgresStorage`. `SessionManager.create_session` /
`create_branch` already mint a `node_id` (`uuid4()`) per session,
stored under `session.metadata["node_id"]`, with the documented
purpose "for independent version vectors."

That scope is deliberate and correct for what ADR-007 set out to
solve: multiple agents writing through **one shared `RuntimeStorage`
backend**, where `merge_branch` can always read every session's
operation-log rows directly because they live in the same database.

The multi-agent swarm design under discussion (Supervisor / Critic /
Worker roles, gossip dissemination) implies something ADR-007 never
addressed: agents running as **separate Runtime processes**, each
against its own local storage, possibly disconnected for periods,
that must reconcile without a shared database and without a central
coordinator. ADR-007's own "Alternatives Considered" section rejected
full CRDT field types for crossing the Core/Runtime boundary drawn in
ADR-001 (this repo's) — it did not evaluate cross-process replication
at all, since it assumed one shared storage instance throughout.

Two architectural invariants from the Runtime Roadmap bound the
solution space:

- **Transport independence** — Runtime must not depend on a specific
  transport; adapters depend on Runtime, never the reverse.
- **Storage independence** — a capability must be opt-in and
  no-op-by-default on `RuntimeStorage`, the same pattern
  `enqueue_task` / `save_object_embeddings` / the ADR-007 operation
  log already use, so backends that don't implement it are unaffected.

---

# Problem

1. **`node_id` is not a durable identity.** It is freshly minted per
   session. Two sessions belonging to the "same" logical agent —
   e.g. an agent process that restarts, or opens a second working
   branch — look like two unrelated clock sources. A gossip peer
   doing anti-entropy needs to recognize "this is the same node I
   talked to yesterday," which today's `node_id` cannot express.
2. **Nothing transmits `cks_operation_log` rows or `VersionVector`s
   between independent storage instances.** Both are read today only
   by `MergeOperation` against the local backend. There is no
   serialization path out, and no path to apply foreign rows into a
   storage instance that didn't produce them.
3. **Nothing decides when to gossip, to whom, or how to escalate a
   conflict found outside a synchronous caller.** `merge_branch`
   today raises `RuntimeMergeConflictError` to whoever called it. A
   background reconciliation cycle has no such caller.

---

# Decision (proposed)

## 1. Persistent `replica_id`, distinct from per-session `node_id`

A new identity, one per Runtime *process/deployment*, generated once
and persisted by the storage backend on first initialization — a
single-row `cks_runtime_identity` table, created lazily the same way
`cks_operation_log` already is. Every session created by that process
bumps its `VersionVector` under `replica_id` in addition to (not
instead of) its existing per-session `node_id`; `VersionVector.clocks`
is already a generic `dict[str, int]`, so mixing replica-scoped and
session-scoped keys requires no change to `bump` / `observe` /
`dominates` / `absorb`. Per-session `node_id` keeps doing exactly the
job ADR-007 gave it — local fast-path disambiguation between
concurrent branches from the same replica. `replica_id` adds a
coarser, stable identity gossip can anchor to across restarts.

## 2. `GossipAdapter`, following the existing Adapter pattern

A new adapter, the same family as the MCP / HTTP / CLI adapters
(ADR-006), owns:

- **Anti-entropy exchange:** periodically trade
  `{replica_id: VersionVector}` summaries with configured peers, over
  a `GossipTransport` protocol kept swappable (reference
  implementation: HTTP long-poll between adapter instances) —
  matching Runtime's transport-independence principle exactly.
- **Fetch on gap:** for any peer whose vector this replica doesn't
  dominate, request `cks_operation_log` rows for the missing
  `(replica_id, clock)` range via two new methods added
  no-op-by-default to `RuntimeStorage` —
  `fetch_operations_since(vector)` and `apply_remote_operations(rows)`
  — following the exact opt-in pattern `enqueue_task` /
  `save_object_embeddings` already established.
- **Apply through the existing fast path, unchanged:** fetched rows
  are handed to the same `MergeOperation` logic ADR-007 already
  built — field-disjoint changes auto-apply, overlapping changes
  raise `RuntimeMergeConflictError` exactly as today. Gossip is a new
  *source* of operations for a merge mechanism that already knows how
  to run one; it introduces no new conflict semantics of its own.

## 3. Conflict escalation via `EventBus`, not a synchronous exception

A same-process `merge_branch` call raising synchronously makes sense
when a human or LLM agent is waiting on the call. A background gossip
cycle has no such caller. `GossipAdapter` catches
`RuntimeMergeConflictError` from the fetch-and-apply step and instead
emits a `RuntimeEvent` (`GossipConflictDetected`, following the
existing lifecycle-event pattern in `events/event_bus.py`) carrying
the same field-level diff `merge_branch` already computes. A
subscriber — e.g. a future Critic agent in `cks-mcp` — resolves it
later through the ordinary `merge_branch` tool. `EventBus` stays the
one integration point; no new callback mechanism is introduced.

---

# Non-Goals

- **Not reopening full CRDT field types.** ADR-007 already rejected
  imposing a specific CRDT representation on every Core field, for
  crossing the Core/Runtime boundary. Gossip transmits the same
  operation-log rows and reuses the same field-disjoint fast path, so
  it inherits that decision rather than revisiting it.
- **Not solving Byzantine or malicious peers.** Gossip here assumes
  cooperating agents within one deployment. Authenticity of a remote
  row, as opposed to its mergeability, is a `cks-mcp`-layer concern
  (HMAC provenance, that repo's ADR-002), not addressed here.
- **Not solving operation-log retention/compaction.** Already an open
  problem ADR-007 flagged (`cks_operation_log` grows unboundedly).
  Gossip makes it somewhat more urgent — a long-disconnected peer
  needs a longer replay range — but doesn't change the shape of the
  problem.
- **Not choosing a production transport.** `GossipTransport` is a
  protocol; the reference implementation (HTTP long-poll) is
  intentionally minimal, not a recommendation of gRPC/libp2p/etc.

---

# Alternatives Considered

## Shared central storage for all agents instead of gossip

Rejected as the general solution, kept as the default for co-located
agents: it already works today via ADR-007 unmodified, requiring no
new code. Gossip specifically targets the disconnected /
independent-storage case the swarm design calls for; it is not
proposed as a replacement for the shared-storage path.

## Reusing per-session `node_id` as the gossip identity

Rejected. `node_id`'s entire documented purpose today is
session-scoped disambiguation. Overloading it to also mean "durable
cross-process identity" would silently break the moment an agent
process restarts and mints new sessions — exactly the failure mode
this ADR exists to avoid.

---

# Consequences

## Positive

- Agents with independent local storage converge without a shared
  database or central coordinator, reusing mechanisms — operation
  log, version vectors, three-way merge — that already exist and are
  already tested. Gossip is additive plumbing, not a new conflict
  model.
- Follows the Adapter pattern exactly, so Runtime itself gains no new
  transport dependency and stays consistent with its own
  architectural invariants.

## Negative

- `replica_id` is new durable state — another migration for existing
  SQLite/Postgres deployments.
- `fetch_operations_since` is one more no-op-by-default method every
  backend maintainer must eventually decide whether to implement.
  (`apply_remote_operations`, originally proposed alongside it below,
  was dropped during implementation — see the 2026-08-01 revision
  note above; applying a remote snapshot goes through the existing
  `MergeOperation`/`commit_transaction` path instead of a dedicated
  storage method.)
- Long-disconnected peers replaying a large operation-log range
  raises the retention/compaction question from ADR-007 from
  "eventually" to "soon."

---

# Status

Partially implemented as of the 2026-08-01 revision note above:
persistent `replica_id`, the operation-log storage methods, and
`GossipAdapter`'s session-snapshot reconciliation (via the existing
ADR-007 `MergeOperation`/`VersionVector` machinery) are implemented
and unit-tested. Still open: the actual peer transport
(`GossipTransport` has no reference implementation yet), scheduling
of anti-entropy cycles, and bootstrapping a session neither replica
has seen before (out of scope for `GossipAdapter` as written — see
Non-Goals). Depends on ADR-007's operation log and version vectors
(implemented).