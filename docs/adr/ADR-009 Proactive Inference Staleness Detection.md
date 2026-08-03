# ADR-009

# Proactive Inference Staleness Detection: A Background Sweeper for Reasoning Conflicts

**Status:** Proposed

**Date:** 2026-08-03

**Category:** Architecture Decision Record

---

# Context

`cks-core` (ADR-001 "Reasoning Objects", ADR-002 "Belief Revision
Support") ships two opt-in `WARNING`-severity constraints relevant
here: `InferenceConfidenceConflictConstraint`
(`CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT` — two or more active
`InferenceStep`s reach the same conclusion with disagreeing
`confidence`) and `StalePremiseConstraint`
(`CKS-EXT-STALE-PREMISE` — an active `InferenceStep` still cites, as
a premise, another `InferenceStep` that has itself since been
superseded). Both live in `OPTIONAL_CONSTRAINTS_BY_NAME` and are
never run unless a caller opts in via `extra_constraints`.

Today that opt-in happens in exactly three places, all synchronous
and all requiring a caller who already suspects something is wrong:

1. `validate_knowledge` / `evolve_knowledge` (`cks-mcp`) accept an
   `extensions` list. `evolve_knowledge` fixed in v1.27.1 to stop
   discarding a `WARNING` once a commit succeeds — but only for
   diagnostics produced by *that caller's own* commit. Whoever
   holds `step-b` never learns that a **different** agent's commit
   just made it stale, unless that same agent happens to call
   `evolve_knowledge`/`validate_knowledge` again with
   `inference_confidence_conflict`/`stale_premise` requested.
2. `arbitrate_inference_conflict` resolves a conflict a caller
   already knows the `conclusion_id` for.
3. `explain_inference` (`ExplainInferenceOperation`,
   `cks_runtime/operations/operation_types.py`) explains one
   `object_id` at a time, on request.

Nothing walks a session's `knowledge_structure` on its own. A
session's own `diagnostics` field (`RuntimeSession.diagnostics`,
`cks_runtime/session/session.py`) and `Runtime.diagnostics`
(populated by `ExecutionPipeline._validate`/`_collect_diagnostics`,
`cks_runtime/pipeline/execution_pipeline.py`) are not a cache of this
either — `_validate` there calls `core_bridge.validate()` with no
`extra_constraints`, so it never runs these two constraints; neither
field is populated by the reasoning extensions at all. There is no
per-session record of "the last time anyone checked this" to read
instead of re-validating.

`cks-runtime` already has the two building blocks this ADR combines.
`GarbageCollector` (`cks_runtime/gc/garbage_collector.py`) is a
background `asyncio.Task` that periodically calls a
`RuntimeStorage` method returning session candidates
(`list_sessions_modified_before`), is a no-op against a backend that
doesn't implement it, and is wired into `Runtime.__init__`/`create`/
`aclose` behind a `RuntimeConfig` flag (`gc_retention`). Separately,
`GossipConflictDetected` (ADR-008) already establishes the pattern
for surfacing a conflict a background process finds with no
synchronous caller to raise to: publish a `RuntimeEvent` on
`EventBus` instead. `cks-mcp`'s `ConflictInbox`
(`src/cks_mcp/conflict_inbox.py`) already subscribes to that event
and queues it for a Critic agent to drain via `list_gossip_conflicts`
— proof this event → inbox → polling-tool shape works end to end for
exactly this kind of "someone should look at this" finding.

---

# Problem

1. **A conflict between two `InferenceStep`s owned by different
   agents is only found if someone happens to re-check.** Agent A
   commits a step superseding a premise Agent B's active step cites.
   Nothing tells B. If B's session sees no further commits, the
   `CKS-EXT-STALE-PREMISE` condition it now carries is never
   evaluated by anyone, indefinitely.
2. **`evolve_knowledge`'s per-commit surfacing (v1.27.1) only covers
   the committing agent's own diagnostics from its own commit.** It
   is a real fix for the case it targets, not a substitute for
   catching a condition that arises without any further commit at
   all in the affected session.
3. **There is no scheduling primitive for "periodically re-check
   reasoning constraints across sessions."** `GarbageCollector`'s
   loop exists but is answering a different question (age since last
   modification, for eviction) with a different candidate set (only
   closed sessions).

---

# Decision (proposed)

## 1. `RuntimeStorage.list_sessions_modified_since`, mirroring `list_sessions_modified_before`

A new no-op-by-default method, same signature shape as its GC
counterpart:

```python
def list_sessions_modified_since(
    self, watermark: datetime, limit: int = 1000,
) -> list[RuntimeSession]:
    """Return sessions with modified_at >= watermark, oldest first. Empty by default."""
    return []
```

`SQLiteStorage`/`PostgresStorage` implement it against the same
indexed `modified_at` column `list_sessions_modified_before` already
queries (`idx_sessions_modified_at`), just with the comparison
flipped and ascending order — no new column, no new index,
`InMemoryStorage` stays unsupported exactly as it is for GC today.

## 2. `InferenceStalenessSweeper`, a `GarbageCollector`-adjacent background worker

Same shape as `GarbageCollector` deliberately, not a shared instance
of it (see Alternatives Considered): `asyncio.Task` loop, duck-typed
against `list_sessions_modified_since`, `start()`/`stop()`/
`run_once()`, batch-limited.

```python
class InferenceStalenessSweeper:
    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        events: EventBus,
        *,
        sweep_interval: float = 300.0,   # 5 minutes
        batch_size: int = 100,
    ) -> None: ...
```

Each sweep:

- Captures `sweep_started_at = datetime.now(UTC)` **before** querying
  — the watermark for the *next* sweep is this timestamp, not the
  latest `modified_at` seen in results, so a session modified
  concurrently with this sweep is never skipped by a race between
  "read candidates" and "advance watermark."
- Calls `list_sessions_modified_since(self._watermark, self._batch_size)`.
- For each candidate, runs `cks.validate(session.knowledge_structure,
  extra_constraints=[OPTIONAL_CONSTRAINTS_BY_NAME["inference_confidence_conflict"],
  OPTIONAL_CONSTRAINTS_BY_NAME["stale_premise"]])` — imported directly
  from `cks.constraints`, the same registry `cks-mcp`'s
  `resolve_extensions` already draws from, so the sweeper needs no
  dependency on `cks-mcp` and can never drift out of sync with what
  those extension names mean.
- Diffs the resulting diagnostics against an in-memory
  `dict[session_id, set[(identity, location)]]` of what this
  sweeper instance last found for that session. Publishes
  `InferenceConflictDetected` (new event, below) only for entries
  not in that set — an unresolved `WARNING` that persists across
  many sweeps is reported once, not every `sweep_interval`. Entries
  that disappear (resolved) are dropped from the set silently.
- Only advances `self._watermark` to `sweep_started_at` when
  `len(candidates) < batch_size` — a full batch means the window
  wasn't drained, so the same watermark is retried next sweep rather
  than risking a candidate past the batch cutoff being skipped
  entirely (see Consequences).

## 3. New event: `InferenceConflictDetected`

Added to `cks_runtime/events/runtime_event.py`, alongside
`GossipConflictDetected` but its own type — not a repurposing of it
(see Alternatives Considered):

```python
@dataclass(frozen=True, slots=True)
class InferenceConflictDetected(RuntimeEvent):
    session_id: str = ""
    version_id: str = ""       # session's latest version_id when observed
    diagnostics: list[Any] = field(default_factory=list)  # code/severity/message/location dicts
```

`version_id` lets a subscriber tell whether the finding is already
stale by the time it's handled — the same reason `VersionCreated`
carries one.

## 4. Wiring: `RuntimeConfig`, `Runtime.__init__`/`create`/`shutdown`

Mirrors `gc_retention`/`gc_sweep_interval`/`gc_batch_size`'s
None-disables shape, but defaults to disabled rather than mirroring
`gc_retention`'s default-on 24h window: `inference_sweep_interval:
float | None = None`, `inference_sweep_batch_size: int = 100`. GC's
default-on posture is safe because eviction only ever touches
already-`closed` sessions; a sweep that publishes `EventBus` events
is a new observable side effect, so it starts opt-in until there's
operational experience with its noise level. `Runtime.__init__`
constructs `InferenceStalenessSweeper` when the interval is set; the
`create()` classmethod starts it (same as `self._gc.start()` at line
332); `aclose()` stops it (same as `self._gc.stop()`) — the same two
call sites `self._gc` already uses.

## 5. Consumption: out of scope here, but unblocked

A `cks-mcp`-side subscriber mirroring `ConflictInbox` — enqueue
`InferenceConflictDetected` records, drain via a
`list_inference_conflicts` tool analogous to `list_gossip_conflicts`
— is the natural next step (closes open item #2, the "Critic loop"
gap, from the same review that produced this ADR) but is a `cks-mcp`
change with its own ADR, not decided here.

---

# Non-Goals

- **Not auto-resolving anything.** Detection and escalation only,
  exactly as `GossipConflictDetected` does for merge conflicts.
  Resolution still goes through `arbitrate_inference_conflict` or
  `evolve_knowledge`'s `resolve_inference_conflict` operation.
- **Not running arbitrary/all validation extensions.** Scoped to
  exactly `inference_confidence_conflict` and `stale_premise` — the
  two constraints whose triggering condition ("another agent's
  commit changed something you cited or agreed with") is specifically
  about content nobody local re-validates. Generalizing to arbitrary
  opted-in extensions is future work, not this ADR.
- **Not solving cross-session staleness.** Both constraints evaluate
  a single `knowledge_structure`; a premise citation and the step it
  cites always live in the same session's structure (an `InferenceStep`
  cited as a premise is a reference within one structure, not across
  branches). A conflict spanning two sessions only becomes visible
  once they're merged (`merge_branch`, ADR-007) into one structure —
  at which point this sweeper picks it up on its next pass over the
  merged session like any other. No separate cross-session mechanism
  is proposed.
- **Not persisting the dedup set across restarts.** The
  `dict[session_id, set[...]]` is in-memory and process-local, the
  same trade-off `ConflictInbox` already makes for its own queue. A
  restart may re-report an unresolved conflict once; accepted as
  cheaper than adding durable dedup state for a `WARNING`-severity
  finding.
- **Not the `cks-mcp`-side inbox/tool.** See Decision point 5.

---

# Alternatives Considered

## Always running the reasoning extensions synchronously on every commit

Rejected. Would change `evolve_knowledge`/the core commit path's
default cost for every caller regardless of whether they use
`InferenceStep`s at all, and — the actual point of this ADR — still
wouldn't catch the motivating case: a session nobody commits to again
after the condition arises. A synchronous check only ever fires on
the next write to the *same* session; the gap here is specifically
sessions that stop being written to.

## Extending `GarbageCollector` itself to also run this check

Rejected. Different lifecycles pulling in opposite directions: GC's
candidate set is explicitly `closed` sessions past a retention
window (`session.closed == False` is skipped unconditionally); a
fresh reasoning conflict is most actionable in exactly the sessions
GC ignores — open, actively-worked ones. Conflating them would force
one cadence/config (`gc_retention`, tuned for eviction safety) onto
an unrelated concern (conflict latency, which wants to be checked
much sooner than 24 hours).

## Reusing `GossipConflictDetected` instead of a new event type

Rejected. Semantically different failures — a merge conflict between
two replicas' operation logs (ADR-008) versus a single-structure
belief conflict (ADR-002) — with fields that don't apply both ways
(`source_replica_id`/`source_session_id` describe a remote peer;
there is no remote peer here). Reusing the type would force every
existing `GossipConflictDetected` subscriber to add a discriminating
check it doesn't need today.

---

# Consequences

## Positive

- Closes the gap `evolve_knowledge`'s v1.27.1 fix explicitly doesn't:
  a conflict that arises without a triggering commit in the affected
  session is now found within one `sweep_interval` instead of never.
- Reuses `GarbageCollector`'s worker shape, `EventBus`, and
  `OPTIONAL_CONSTRAINTS_BY_NAME` wholesale — no new constraint logic,
  no new conflict semantics, no new Core/Runtime dependency.
- `cks-mcp`'s `ConflictInbox` pattern already proves the
  event-to-polling-tool path works; wiring an
  `InferenceConflictDetected` subscriber there is expected to be a
  small, mechanical follow-up rather than new design.

## Negative

- One more no-op-by-default `RuntimeStorage` method
  (`list_sessions_modified_since`) every backend maintainer decides
  whether to implement — same category of cost ADR-008 already
  flagged for `fetch_operations_since`.
- One more background `asyncio.Task` lifecycle to manage
  (start/stop wiring, config flag, docs).
- The batch-limited watermark (Decision point 2, last bullet) means
  a deployment with more than `batch_size` sessions modified within
  one `sweep_interval` makes no forward progress on its watermark
  until a sweep drains the backlog below the batch size — a
  degenerate but real case worth monitoring, not solved by pagination
  here (kept simple, matching GC's own lack of pagination).
- In-memory, per-process dedup means a restart (or a multi-process
  deployment with more than one sweeper instance) can re-publish an
  already-known finding. Acceptable for a `WARNING`-severity,
  non-destructive notification; would not be acceptable if this
  pattern were reused for something that triggers an action on
  receipt.

---

# Status

Proposed. Not implemented. Depends on `cks-core`'s
`InferenceConfidenceConflictConstraint`/`StalePremiseConstraint`
(ADR-001/ADR-002, implemented) and `OPTIONAL_CONSTRAINTS_BY_NAME`
(implemented), and reuses `cks-runtime`'s `GarbageCollector` shape
and `EventBus` (ADR-006, implemented) unmodified. The `cks-mcp`-side
consumer (Decision point 5) is unblocked by this ADR but not decided
by it.
