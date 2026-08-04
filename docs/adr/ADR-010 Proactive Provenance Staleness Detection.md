# ADR-010: Proactive Provenance Staleness Detection

**Status:** Proposed
**Related:** ADR-009 (Proactive Inference Staleness Detection), ADR-002 (cks-mcp, Provenance Signing), ADR-008 (Gossip Replication)

## Context

ADR-009 gave `cks-runtime` a background sweeper (`InferenceStalenessSweeper`)
that periodically walks active `InferenceStep`s, detects when a premise has
gone stale (superseded since the step was written), and escalates an
`InferenceConfidenceConflict` for the Critic Agent to resolve. This closed
the gap where a stale inference could sit undetected until something
happened to query it.

`VerificationRecord`s (see cks-mcp ADR-002, Provenance Signing) have the
same class of problem and no equivalent safety net. `verify_source`
performs a real HTTP check *at the moment it is called* and cryptographically
signs the result. After that, the record is trusted indefinitely:

- The source page can change its content without anyone re-checking.
- The source URL can go dead (link rot) without anyone re-checking.
- Nothing currently distinguishes "verified five minutes ago" from
  "verified fourteen months ago" at query time.

There is no proactive mechanism that revisits a `VerificationRecord` after
it was created. This is the provenance-side analogue of the gap ADR-009
closed for inference.

## Decision

Add `cks_runtime.reasoning.provenance_staleness_sweeper`, structurally
parallel to `InferenceStalenessSweeper`:

- Runs on the same background-task infrastructure already used by
  `InferenceStalenessSweeper` and `OutboxEmbeddingWorker` (see
  `cks_runtime.projection.outbox_worker`).
- Periodically scans `VerificationRecord` objects whose `verified_at`
  timestamp is older than a configurable TTL (`CKS_PROVENANCE_TTL_SECONDS`,
  default 30 days -- mirroring `CKS_INFERENCE_STALENESS_*` env var naming).
- For each expired record, does **not** re-verify itself (that would
  require making the same outbound HTTP call `verify_source` makes, which
  is cks-mcp's responsibility, not runtime's) -- instead it escalates a new
  conflict class, `ProvenanceStalenessConflict`, into the same outbox table
  `cks_outbox_tasks` uses for `gossip_conflict` / `inference_conflict`,
  with `task_type = "provenance_conflict"`.
- The existing `claim_conflict_task` / `complete_conflict_task` /
  `fail_conflict_task` / `dead_letter_conflict_task` tools are reused
  unchanged -- `task_type` was already a free-form string, so this needs
  no schema change to the outbox itself.

## Consequences

- **Reuses existing infrastructure.** No new table, no new storage method
  beyond what ADR-009 and the outbox pattern already ship. The sweeper adds
  one new task_type value to an already-generic queue.
- **Runtime still does not perform I/O against external sources.** The
  sweeper only *detects* staleness by timestamp; the actual re-verification
  HTTP call stays in `cks-mcp` (`verify_source`), preserving the same
  Runtime/Core-adjacent separation of concerns ADR-001 (Runtime Layering)
  established: Runtime orchestrates, it does not originate.
- **cks-mcp must add a consumer.** `resolve_inference_conflict`'s
  cks-mcp counterpart for inference conflicts is `arbitrate_inference_conflict`;
  this ADR requires a symmetric cks-mcp tool (`refresh_verification`) and a
  `critic_agent` dispatch branch for `task_type == "provenance_conflict"`.
  See the cks-mcp-side ADR and `critic_agent.py`'s resolution-policy
  docstring, which will need a third bullet alongside `gossip_conflict` and
  `inference_conflict`.
- **Configuration surface grows by one TTL.** Consistent with existing
  `CKS_*` env var conventions; documented in `docs/getting-started.md`'s
  environment variable table when implemented.

## Alternatives considered

- **Re-verify inline inside the sweeper.** Rejected: would require Runtime
  to make outbound HTTP requests and hold cryptographic signing material,
  both of which currently live in cks-mcp (`cks_mcp.provenance`). Keeping
  the sweeper detection-only preserves the existing module boundary.
- **No TTL, only link-rot detection.** Rejected as insufficient on its
  own: a source can change its *content* materially without ever 404ing,
  which a HEAD-request-only check would miss. TTL-based re-verification
  catches both cases at the cost of periodic re-checks.