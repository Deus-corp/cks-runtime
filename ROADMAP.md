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
- Gossip replication, CRDT adapter, duplicate replica ID detection — **done**.

**Autonomous Sweepers**
- Seven background sweepers with observability and remote control — **done**.

**Agent Infrastructure**
- Agent liveness, stop signalling, outbox task filtering, dead-letter queue — **done**.

**Storage & Persistence**
- PostgreSQL + pgvector, graph registry, backup/restore — **done**.

**Execution Engine**
- `ValidateOperation`, `EvolveOperation`, `SerializeOperation`, `ExplainOperation` — **done**.
- Dependency resolution, parallel execution, retry, compensation — **open**.

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

# Next Up — Runtime Platform 2.0

## Execution Engine
- Dependency resolution and parallel execution.
- Retry and compensation.

## Distributed Runtime
- Runtime Cluster / Shared Storage / Distributed Event Bus.
- General-purpose WebSocket/SSE event stream.

## Observability Platform
- Timeline, replay, heatmap, profiler.

## Runtime Scheduler
- General-purpose scheduler beyond reasoning sweepers.

## Runtime Introspection
- Self-describing runtime: list capabilities, endpoints, storage config.

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
