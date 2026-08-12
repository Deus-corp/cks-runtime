# CKS Runtime Roadmap

This roadmap outlines the planned evolution of CKS Runtime — the canonical operational environment for Canonical Knowledge Structures.

The roadmap is intentionally incremental. For the project's mission, vision, and architectural principles, see `docs/charter/CHARTER.md` and `docs/architecture/ARCH-001_Runtime_Architecture.md`; this document tracks *what ships and when*, not why the project exists.

---

# Guiding Direction

Runtime owns operational execution — sessions, transactions, persistence, versioning, diagnostics, events, and adapters. CKS Core remains the single source of semantic truth; Runtime never becomes a second semantic engine.

---

# Current Status (August 2026 — v1.49.2)

Runtime 1.0 was reached and substantially surpassed. Verified against `CHANGELOG.md`:

**Distributed Runtime & Replication**
- Gossip-based replication between runtime nodes (`GossipService`, `PeerDiscovery`, weighted peer selection with backoff) — **done** (ADR-008).
- CRDT adapter: grow-only set + Merkle prefix tree (Stage 1), MV-Register with causal ordering and fork detection (Stage 2), and quarantine validation wired into the gossip merge path — **done** (ADR-013).
- Duplicate replica ID detection, blocking silent divergence — **done** (v1.49.2).

**Autonomous Sweepers**
Seven background sweepers now run in-process, each detection-only (they escalate findings into the outbox rather than acting unilaterally):
- `InferenceStalenessSweeper` (ADR-009), `ProvenanceStalenessSweeper` (ADR-010), `TemporalStalenessSweeper` (ADR-011), `ContradictionSweeper`, `GraphFreshnessSweeper`, `GraphAutoUpdateSweeper`, `GraphHealthSweeper` — **done**.
- Shared observability (`SweeperStatusMixin`, `list_agent_statuses()`) and remote start/stop control via persisted overrides (ADR-015) — **done**.

**Agent Infrastructure**
- Agent liveness tracking (`cks_agent_liveness`) and standalone-agent stop signalling (ADR-016), supporting external Critic/Enrichment/Fork Resolution/Pipeline agent processes — **done**.
- Persistent outbox: task-type filtering, dead-letter queue, batch peek/drain by type — **done**.
- `AgentStepStarted`/`AgentStepCompleted` runtime events for pipeline observability — **done**.

**Storage & Persistence**
- PostgreSQL backend (async storage ABC, pgvector support) alongside SQLite — **done**.
- Graph registry (`register_graph`/`get_graph`/`list_graphs`), the storage foundation for `cks-mcp`'s Memory Agent — **done**.
- Backup and disaster recovery: `export_storage()` / `import_storage()` across all backends (ADR-012) — **done**.

**Execution Engine**
- `ValidateOperation`, `EvolveOperation`, `SerializeOperation`, `ExplainOperation` via `CoreBridge` — **in progress**. Dependency resolution, parallel execution, retry, and compensation are still open — see Version 2.0 below.

The sub-items under "Version 1.x" below (reliability, observability, storage, distributed, plugin platform, performance, security, deployment, LTS) have not all been individually re-verified against `CHANGELOG.md` — Distributed Runtime and parts of Storage are confirmed done above; the rest should get a dedicated audit pass rather than being assumed complete.

---

# Version 1.x — Production Runtime

Thematic areas beyond the Current Status verification above:

- **Reliability** — recovery, snapshots, crash-safe execution, lease management.
- **Observability** — metrics, tracing, execution timeline, profiling.
- **Storage** — migration framework, backup API (backup/restore itself is done via ADR-012 above).
- **Distributed Runtime** — replication is done (ADR-008); distributed transactions and leader election remain open.
- **Plugin Platform** — plugin SDK, registry, dynamic loading, compatibility management.
- **Performance** — pipeline optimisation, parallel execution, caching, memory optimisation.
- **Security** — authentication, authorization, capability model, sandboxing, audit trail.
- **Deployment** — Kubernetes/Helm/Runtime Service are not currently planned; Docker distribution was considered and intentionally descoped.
- **Long Term Support** — operational hardening, API stability guarantees.

---

# Version 2.0 — Runtime Platform (next up)

**Goal:** Runtime becomes self-describing.

Status of each sub-goal, verified against `CHANGELOG.md`:

| Sub-goal | Status |
|---|---|
| Execution Engine | In progress — see Current Status above. |
| Distributed Runtime | In progress — Replication shipped (ADR-008); Runtime Cluster, Shared Storage, and a general Distributed Event Bus still open. |
| Reactive Runtime | Partial — the Event Bus is extensively used, but not everything is event-driven yet. |
| Runtime Scheduler | Not started as a general-purpose feature (the reasoning sweepers do periodic background execution, but that's narrower than this goal). |
| Runtime Graph | Not started. |
| Runtime Introspection | Not started. |
| Execution Plans | Not started. |
| Runtime DSL | Not started. |
| Observability Platform | Not started as a unified platform (basic metrics/event logs exist via `cks-mcp`'s `get_metrics`, but no Timeline/Replay/Heatmap/Explorer/Profiler). |
| Runtime Studio | Not started. |

---

# Platform Evolution (long-term, unscheduled)

- **Version 3.x — Cloud Runtime:** multi-tenancy, horizontal scaling, federation, managed Runtime.
- **Version 4.x — Autonomous Runtime:** adaptive scheduling, self-healing, policy engine.
- **Version 5.x — Semantic Operating Environment:** Runtime as the operational kernel of the complete CKS ecosystem.

---

# Compatibility Policy

- **Before Runtime 1.0:** architecture could evolve when necessary.
- **After Runtime 1.0:** patch releases fix defects only; minor releases add backward-compatible functionality; major releases introduce architectural changes.

---

# Project Philosophy

CKS Runtime favours architectural stability over implementation complexity.

Every capability must preserve:

- Runtime/Core separation
- Operational determinism
- Storage independence
- Transport independence
- Adapter independence
- Plugin independence
- Semantic authority of CKS Core

CKS Runtime shall never become a second semantic engine. CKS Core remains the single source of semantic truth.

The roadmap may evolve as the specifications mature and the sweeper/agent ecosystem grows.
