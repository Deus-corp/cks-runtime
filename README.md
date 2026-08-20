# CKS Runtime

> The canonical operational environment for Canonical Knowledge Structures.

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-872%20passing-brightgreen)
[![PyPI](https://img.shields.io/pypi/v/cks-runtime)](https://pypi.org/project/cks-runtime/)

> 🚀 **[Live demo →](https://punctumactus.github.io/cks-website/demo/demo.html)** — explore the CKS ecosystem graph directly in your browser, no server required.

CKS Runtime is the canonical execution environment for
Canonical Knowledge Structures (CKS).

Where **CKS Core** defines the semantics of knowledge,
**CKS Runtime** defines its operational lifecycle.

Runtime provides the infrastructure required to execute,
manage, version, persist and expose Canonical Knowledge Structures
without becoming a semantic authority itself.
**Now fully async** with PostgreSQL support.

---

# Ecosystem

CKS Runtime is part of a family of interoperable projects built on the
Canonical Knowledge Structure.

| Project | Description | Repository |
|---------|-------------|------------|
| **cks-core** | Canonical semantic engine – the single source of canonical truth. | [cks-core](https://github.com/PunctumActus/cks-core) |
| **cks-runtime** | Operational environment – sessions, transactions, persistence. | [cks-runtime](https://github.com/PunctumActus/cks-runtime) |
| **cks-mcp** | MCP server – exposes CKS to LLMs and autonomous agents. | [cks-mcp](https://github.com/PunctumActus/cks-mcp) |
| **cks-studio** | Visual workspace – explore, monitor, and manage graphs. | [cks-studio](https://github.com/PunctumActus/cks-studio) |
| **cks-website** | Documentation & demo site. | [cks-website](https://github.com/PunctumActus/cks-website) |

📖 **Full documentation, case studies, and an interactive demo**
are available at the **[CKS Documentation Site](https://punctumactus.github.io/cks-website/)**.

---

# Why Runtime?

Canonical knowledge is immutable.

Operational state is not.

Applications need to:

- create sessions
- execute transactions
- maintain history
- persist state
- expose APIs
- coordinate diagnostics

These responsibilities belong to Runtime rather than CKS Core.

```
Canonical Knowledge Structure
            │
            ▼
        CKS Runtime
            │
 ┌──────────┼──────────┐
 ▼          ▼          ▼
Session  Versioning  Storage
```

Runtime manages operational behaviour.

CKS Core defines semantic behaviour.

---

# Core Principles

CKS Runtime is founded on four architectural principles.

### Runtime is not a semantic authority.

Semantic meaning permanently belongs to CKS Core.

Runtime never redefines knowledge.

---

### Runtime orchestrates semantic services.

Validation.

Evolution.

Serialization.

Diagnostics.

These services originate from CKS Core.

Runtime coordinates their execution.

---

### Operational state belongs to Runtime.

Sessions.

Transactions.

Persistence.

Version History.

These are Runtime responsibilities.

---

### Observable behaviour is standardized.

The Runtime Standard specifies observable operational behaviour rather than implementation techniques.

---

# Runtime Architecture

The CKS ecosystem is organized into four architectural layers.

```
Applications
        │
        ▼
Adapters
        │
        ▼
CKS Runtime
        │
        ▼
Public CKS Core API
        │
        ▼
CKS Core
```

Responsibilities are strictly separated.

| Layer | Responsibility |
|--------|----------------|
| CKS Core | Semantic authority |
| CKS Runtime | Operational orchestration |
| Adapters | Protocol exposure |
| Applications | Business logic |

---

The current Reference Runtime provides:

- Runtime Sessions
- Transaction Management
- Version History
- Storage Abstraction
- Runtime Diagnostics
- Explainability Coordination
- Canonical Runtime API
- Reference Runtime Architecture
- Runtime Conformance Model
- **CKS Core Integration** (via `CksCoreAdapter`)
- **Execution Engine** – canonical operations (Validate, Serialize, Explain, Evolve, Diff) via `CoreBridge`
- **Operation Dispatcher** – registry-based operation resolution
- **Event System** – lifecycle events published via `EventBus`
- **Time-Travel Operations** – `ListVersionsOperation`, `RevertVersionOperation`
- **Structural Diff** – compact change computation between versions
- Three‑way merge of knowledge structures via `cks-core`'s `merge()` function
- **Query Subgraph** – k‑hop neighbourhood extraction with type filters and budget/ranking, delegated to cks-core's query_subgraph()
- **Persistent Storage** – SQLite-backed storage via `SQLiteStorage`, surviving server restarts. Configurable through `RuntimeConfig.storage_path`.
- **Indexed & Vectorized Embeddings** – `search_embeddings` uses NumPy matrix operations for ~10× faster similarity search, with a database index for multi-session scalability.
- **PostgreSQL Storage Backend** — production-grade, async connection pooling, JSONB payloads, outbox with `SELECT ... FOR UPDATE SKIP LOCKED`, and pgvector-powered semantic search with HNSW index.
- **Shared Patch Codec** — consistent serialization/deserialization of structural operators across SQLite and Postgres backends.
- **Session Garbage Collector** – background task that automatically archives stale closed sessions, keeping storage compact in long-running deployments. Configurable retention window and sweep interval.
- **Local embeddings via fastembed** – offline, token-free semantic search with `FastEmbedEmbeddingClient` (`pip install cks-runtime[fastembed]`).
- **Gossip Replication (ADR-008)** — peer-to-peer session exchange: replica identity, HMAC-signed envelopes, replay protection, peer discovery (`PeerDiscovery`, `HTTPPeerDiscovery`), weighted peer selection with backoff (`PeerScheduler`), and a background anti-entropy service (`GossipService`). HTTP transport via `aiohttp` (`pip install cks-runtime[gossip]`).
- **CRDT Adapter (ADR-013)** — conflict-free replicated storage layer beneath gossip: a grow-only set with a content-addressed Merkle prefix tree, an MV-Register with causal ordering and automatic fork detection, and quarantine validation (`cks.validate()` + Merkle-identity checks) wired into every incoming merge. Publishes `CRDTForkDetected` events and detects duplicate replica IDs to block silent divergence.
- **Autonomous Sweepers** — seven background, detection-only sweepers that escalate findings into the persistent outbox rather than acting unilaterally: `ContradictionSweeper`, `InferenceStalenessSweeper` (ADR-009), `ProvenanceStalenessSweeper` (ADR-010), `TemporalStalenessSweeper` (ADR-011), `GraphFreshnessSweeper`, `GraphAutoUpdateSweeper`, and `GraphHealthSweeper`. Shared observability via `SweeperStatusMixin`/`list_agent_statuses()`, and remote start/stop control through persisted overrides (ADR-015).
- **Agent Infrastructure** — liveness tracking (`cks_agent_liveness`) and stop signalling (ADR-016) for external standalone agent processes (Critic, Enrichment, Fork Resolution, Pipeline); `AgentStepStarted`/`AgentStepCompleted` events for pipeline observability.
- **Graph Registry** — `register_graph`/`get_graph`/`list_graphs` persist named session references, the storage foundation for `cks-mcp`'s Memory Agent.
- **Backup & Disaster Recovery (ADR-012)** — `export_storage()`/`import_storage()` across every backend for full data dumps and clear/merge restores.
- **Persistent Outbox** — task-type filtering, dead-letter queue (`dead_letter_outbox_task`, `list_dead_letter_tasks`), and batch peek/drain by type, so multiple workers can share one outbox table without stealing each other's tasks.

---

# Design Goals

CKS Runtime is designed to be:

- deterministic
- implementation-independent
- transport-independent
- storage-independent
- session-oriented
- transaction-oriented
- semantically neutral

---

# Relationship to CKS Core

CKS Runtime depends upon CKS Core.

CKS Runtime never replaces CKS Core.

```
CKS Core
    defines semantics

        │

        ▼

CKS Runtime
    orchestrates semantics
    manages operational lifecycle
```

Runtime communicates exclusively through the public CKS Core API.

---

# Installation

From PyPI:

```bash
pip install cks-runtime
```

Or from source:

```bash
git clone https://github.com/PunctumActus/cks-runtime.git

cd cks-runtime

pip install -e .
```

---

# Quick Example

```python
from cks_runtime import Runtime
from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.operations.operation_types import (
    ValidateOperation,
    EvolveOperation,
    ListVersionsOperation,
    RevertVersionOperation,
)

# Create Runtime with real CKS Core
runtime = Runtime(core=CksCoreAdapter())

# Create a session and validate a knowledge structure
session = runtime.create_session({"example": True})
tx = runtime.begin_transaction(session)
tx.add_operation(ValidateOperation("v1", knowledge_structure=session.knowledge_structure))
version = runtime.commit_transaction(tx)

# Evolve the structure
tx2 = runtime.begin_transaction(session)
tx2.add_operation(EvolveOperation("evolve", knowledge_structure=session.knowledge_structure, evolution=[]))
version2 = runtime.commit_transaction(tx2)

# List versions
versions = runtime.executor.execute(ListVersionsOperation(), session)
print(versions.payload)

# Revert to the first version
tx3 = runtime.begin_transaction(session)
tx3.add_operation(RevertVersionOperation("revert", target_version_id=version.version_id))
runtime.commit_transaction(tx3)
```

---

## Storage Backends

CKS Runtime supports pluggable storage backends through a unified async interface (`AsyncRuntimeStorage`):

| Backend | Type | Status | Notes |
|---------|------|--------|-------|
| **InMemoryStorage** | Sync | ✅ Stable | For testing and ephemeral sessions |
| **SQLiteStorage** | Sync | ✅ Stable | Persistent, single‑writer, WAL mode |
| **PostgresStorage** | Async | ✅ Stable | Production‑grade, connection pooling, JSONB, outbox, pgvector embeddings with HNSW index |

Sync backends (`InMemoryStorage`, `SQLiteStorage`) are automatically adapted to the async interface via `SyncStorageAdapter` (using `asyncio.to_thread`), so existing code works unchanged. Use `await Runtime.create(...)` for full async startup, or plain `Runtime(...)` for lightweight testing without persistence.

---

# Documentation

📚 **[CKS Documentation](https://punctumactus.github.io/cks-website/)** — architecture guides, case studies, and API reference across all CKS projects.

The Runtime Standard consists of the following normative specifications.

| Specification | Purpose |
|--------------|---------|
| SPEC-001 | Runtime Overview |
| SPEC-002 | Session Model |
| SPEC-003 | Runtime API |
| SPEC-004 | Diagnostics |
| SPEC-005 | Transactions |
| SPEC-006 | Storage |
| SPEC-007 | Version History |
| SPEC-008 | Runtime Conformance |

Supporting documents include:

- Runtime Charter
- Architectural Analyses
- Architecture Decision Records
- Reference Architecture

---

## Project Status

Current implementation status:

| Component | Status |
|----------|--------|
| Runtime Architecture | ✅ Complete |
| Session Model | ✅ Complete |
| Transaction Model | ✅ Complete |
| Version History | ✅ Complete |
| Diagnostics | ✅ Complete |
| Storage Abstraction | ✅ Complete |
| **Async Runtime** | ✅ Complete |
| **PostgreSQL Backend** | ✅ Complete |
| **Session Garbage Collector** | ✅ Complete |
| Core Integration (CoreBridge) | ✅ Complete |
| Execution Engine (Operations + Dispatcher) | ✅ Complete |
| Event System | ✅ Complete |
| Time-Travel Operations | ✅ Complete |
| Structural Diff | ✅ Complete |
| Query Subgraph | ✅ Complete |
| Persistent Storage (SQLite) | ✅ Complete |
| **Gossip Replication (ADR-008)** | ✅ Complete — peer discovery, anti-entropy, duplicate replica ID detection |
| **CRDT Adapter (ADR-013)** | ✅ Complete — G-Set + Merkle tree, MV-Register, fork detection, quarantine |
| **Autonomous Sweepers** (7) | ✅ Complete — contradiction, inference/provenance/temporal staleness, graph freshness/auto-update/health |
| **Sweeper Control (ADR-015)** | ✅ Complete |
| **Agent Liveness & Control (ADR-016)** | ✅ Complete |
| **Graph Registry** | ✅ Complete |
| **Backup & Disaster Recovery (ADR-012)** | ✅ Complete |
| **Outbox: task-type filter, DLQ** | ✅ Complete |
| Test Suite | ✅ 795 tests passing (+68 requiring optional backends: Postgres, gossip) |

The current implementation serves as the reference implementation of the
CKS Runtime Standard (SPEC-001 … SPEC-008).

Future work focuses on Runtime Platform 2.0: dependency resolution and
parallel execution in the Execution Engine, distributed transactions and
leader election, and a unified observability platform. See `ROADMAP.md`
for the full breakdown.

---

# Long-Term Vision

CKS Runtime aims to become the canonical operational foundation shared by every CKS-compatible implementation.

Future adapter standards—including MCP, CLI, HTTP and others—will rely on Runtime rather than communicating directly with CKS Core.

This preserves a single semantic authority while allowing unlimited operational implementations.

---

# License

MIT
