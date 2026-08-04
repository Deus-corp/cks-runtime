# ADR-011: Temporal Staleness Detection

**Status:** Proposed
**Related:** ADR-009 (Proactive Inference Staleness Detection), ADR-010 (Proactive Provenance Staleness Detection), cks-core ADR-003 (Temporal Validity Constraint)

## Context

cks-core's ADR-003 added `TemporalValidityConstraint`
(`cks.constraints.temporal`, cks-core >= 1.20.0), an opt-in constraint
that checks every object's `structure` for an optional `valid_until`
field (ISO-8601 datetime string) and raises a `WARNING` diagnostic
(`CKS-EXT-TEMPORAL-VALIDITY`) once that window has closed. It is
deliberately minimal: it answers exactly one question -- "has this
fact expired?" -- and nothing more.

That check only ever runs when an agent explicitly calls
`validate_knowledge` with `extensions: ["temporal_validity"]` (or
otherwise opts the constraint in via `extra_constraints`). Nothing in
`cks-runtime` scans sessions proactively for facts that have quietly
expired. A session that accumulates `valid_until` fields and is never
re-validated by hand can carry stale, expired facts indefinitely,
with nothing surfacing the problem until something happens to query
it -- the same gap ADR-009 closed for stale inference premises and
ADR-010 closed for stale provenance records.

## Decision

Add `cks_runtime.reasoning.temporal_staleness_sweeper`, structurally
parallel to `InferenceStalenessSweeper` (ADR-009) and, in particular,
`ProvenanceStalenessSweeper` (ADR-010), whose lifecycle and outbox
escalation shape it follows directly:

- Runs on the same background-task infrastructure already used by
  `InferenceStalenessSweeper`, `ProvenanceStalenessSweeper`, and
  `OutboxEmbeddingWorker`: `start()`/`stop()` manage an `asyncio.Task`,
  and `sweep_once()` runs a single sweep synchronously for tests.
- Periodically walks sessions via `list_sessions_modified_since` (the
  same watermark-advance / growing-batch discipline ADR-009 and
  ADR-010 already established), and for each session's knowledge
  structure calls `cks.validate(structure, extra_constraints=[TemporalValidityConstraint()])`.
- Collects diagnostics with `severity == WARNING` and
  `identity == "CKS-EXT-TEMPORAL-VALIDITY"` -- the exact code
  `TemporalValidityConstraint` reports expired objects under.
- Deduplicates by `(session_id, location)` across sweeps (`location`
  is the expired object's id), mirroring `ProvenanceStalenessSweeper`'s
  `_known_stale` convention, so the same still-unresolved expiry is not
  re-escalated -- and a new outbox task not re-written -- on every
  single sweep (default hourly) for as long as it stays unresolved. If
  a session is later revalidated and the diagnostic no longer appears
  (e.g. `valid_until` was bumped forward, or the object was removed),
  it drops out of the dedup set and a subsequent expiry is escalated
  again rather than suppressed forever.
- For each newly-found expiry, escalates via
  `storage.enqueue_task(task_type="temporal_conflict", session_id=..., payload=...)`
  onto the same outbox table (`cks_outbox_tasks`) already used for
  `gossip_conflict` / `inference_conflict` / `provenance_conflict`
  tasks. The existing `claim_conflict_task` / `complete_conflict_task`
  / `fail_conflict_task` / `dead_letter_conflict_task` tools are reused
  unchanged -- `task_type` was already a free-form string, so this
  needs no schema change to the outbox itself.
- Like `ProvenanceStalenessSweeper`, checks `supports_outbox` and the
  presence of `list_sessions_modified_since` at `start()` time, and is
  a silent no-op on storage backends that don't support one or both
  (e.g. `InMemoryStorage`).

**The sweeper does not modify the knowledge structure itself.** It is
detection-and-escalation only, exactly like `ProvenanceStalenessSweeper`
never re-verifies inline. Resolving an escalated `temporal_conflict`
task -- e.g. deciding whether the expired fact should be archived,
superseded, or have its `valid_until` extended -- is left to a Critic
Agent, via a future `resolve_temporal_conflict` tool in cks-mcp,
symmetric to `arbitrate_inference_conflict` (ADR-009) and
`refresh_verification` (ADR-010).

## Consequences

- **Reuses existing infrastructure.** No new table, no new storage
  method beyond what ADR-009 and ADR-010 already ship. The sweeper
  adds one new `task_type` value (`temporal_conflict`) to an already
  generic outbox queue.
- **Runtime performs no I/O beyond storage.** Unlike
  `ProvenanceStalenessSweeper`, there was never an outbound HTTP call
  to avoid here -- `TemporalValidityConstraint` only compares a
  timestamp already present in the structure against wall-clock time.
  The sweeper's only external dependency is `cks.validate` itself,
  the same dependency `InferenceStalenessSweeper` already has.
- **Configuration surface grows by two fields.** `RuntimeConfig` gains
  `temporal_sweep_interval` (default 3600s, `None` disables the
  sweeper) and `temporal_sweep_batch_size` (default 100), following
  the same naming convention `inference_sweep_*` and
  `provenance_sweep_*` already established.
- **cks-mcp must eventually add a consumer.** This ADR only covers
  detection and escalation in cks-runtime. A future cks-mcp-side ADR
  is needed for `resolve_temporal_conflict` and a `critic_agent.py`
  dispatch branch for `task_type == "temporal_conflict"`, alongside
  the existing `gossip_conflict` / `inference_conflict` /
  `provenance_conflict` branches.
- **Requires cks-core >= 1.20.0.** `TemporalValidityConstraint` is
  only importable from that version onward (see cks-core ADR-003).

## Alternatives considered

- **Fold temporal expiry into `InferenceStalenessSweeper`.** Rejected:
  that sweeper's constraint set (`inference_confidence_conflict`,
  `stale_premise`) is specifically about reasoning-chain staleness,
  not general object-level temporal validity, which can apply to any
  object type, not just `InferenceStep`s. Keeping the two sweepers
  separate keeps each one's `_RELEVANT_CONSTRAINT_NAMES`/dedup surface
  aligned to a single ADR-003/ADR-009 concern, the same separation
  ADR-010 already chose over folding provenance staleness in.
- **Have the sweeper mutate `valid_until` or otherwise "fix" expired
  facts automatically.** Rejected: deciding what an expired fact
  *should* become (archive it, supersede it, extend its window) is a
  judgment call, not a mechanical one -- the same reasoning ADR-010
  applied to leave re-verification in cks-mcp. This sweeper stays
  detection-only, consistent with Runtime orchestrating rather than
  originating decisions (ADR-001, Runtime Layering).
