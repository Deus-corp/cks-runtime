# Changelog

All notable changes to CKS Runtime will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

---

## [1.48.6] - 2026-08-07

### Fixed
- **Deep-freeze serialization bug in `record_operations`** – `record_operations` (SQLite and Postgres) now recursively thaws frozen `MappingProxyType`/`tuple` values in `field_value` before JSON encoding, using the same `_thaw` helper already applied to `patch_codec`. Prevents `Object of type mappingproxy is not JSON serializable` errors when recording operations with nested structures (e.g. pipeline `transition_log` patches).
- New regression test `test_record_operations_thaws_frozen_nested_field_value` added.

---

## [1.48.5] - 2026-08-07

### Added
- **`AgentStepStarted` and `AgentStepCompleted` runtime events** – new event types in `cks_runtime.events.runtime_event`, published by the upcoming `CKSAgentOrchestrator` (ADR-007). Subscribers can observe pipeline progress without coupling to the agent implementation.

### Fixed
- **Deep-freeze serialization bug in `patch_codec.py`** – `serialize_operators` now recursively thaws frozen `MappingProxyType`/`tuple` values in nested structures before JSON serialization. Previously, an object with a nested dict/list (e.g. a `transition_log`) would crash on the *second* commit with `Object of type mappingproxy is not JSON serializable`, because delta patches did not deep-convert structures like full snapshots do.

---

## [1.48.4] - 2026-08-07

### Fixed
- **`OutboxEmbeddingWorker` no longer crashes with `'AddObject' object has no attribute 'object_id'`** – replaced private attribute access with the public `obj` property (available since cks-core v1.14.0). The worker now correctly retrieves the object's identity via `op.obj.identity.id` instead of the non-existent `op.object_id`.

---

## [1.48.3] - 2026-08-07

### Added
- **Thread-safe CRDT storage** – `SQLiteCRDTStore` and its `SQLiteMerkleTree` now share the same `threading.RLock` used by `SQLiteStorage` when wrapping the runtime's own connection. This prevents `sqlite3.InterfaceError` corruption when gossip, the fork agent, and the background embedding worker access the shared SQLite connection concurrently. The lock is injected via `SQLiteCRDTStore.__init__(lock=...)` and forwarded to `SQLiteMerkleTree`; standalone/test usage falls back to a fresh `RLock`.
- **`OutboxEmbeddingWorker.set_embedding_client`** – allows swapping the embedding client at runtime; the new client takes effect on the next poll iteration.

### Changed
- **`Runtime.embedding_client` setter** now pushes the new client into the running `OutboxEmbeddingWorker` (via `set_embedding_client`) in addition to storing it, so a plugin installing a client after `Runtime.create()` correctly updates the worker.

### Fixed
- Concurrent gossip / fork-agent / background worker activity on a shared SQLite database no longer raises `sqlite3.InterfaceError: bad parameter or other API misuse` (verified with stress tests emulating multi‑process access).

---

## [1.48.2] - 2026-08-07

### Added
- **`Runtime.embedding_client` setter** – `FastEmbedPlugin` (and any other plugin) can now properly set the embedding client via `runtime.embedding_client = client`. The setter also pushes the new client into the already running `OutboxEmbeddingWorker` (via new method `set_embedding_client`), so replacing the client at runtime no longer leaves the worker using a stale instance.

### Fixed
- **`AttributeError: property 'embedding_client' of 'Runtime' object has no setter`** – resolved by adding a setter for `embedding_client`.
- **SQLite concurrency** – All methods of `SQLiteStorage` that touch `self._conn` are now protected by a single `threading.RLock` (decorator `@_synchronized`). This eliminates `sqlite3.InterfaceError: bad parameter or other API misuse` when gossip, the fork agent, and the background `OutboxEmbeddingWorker` concurrently access the same database. Verified with stress‑tests (8 threads × 300 iterations, plus a scenario emulating two independent `Runtime` instances with parallel workers) – zero failures.
- Full unit test suite passes (654 tests, 26 skipped).

### Changed
- `OutboxEmbeddingWorker` now supports dynamic embedding client replacement via `set_embedding_client`.

---

## [1.48.1] - 2026-08-07

- Re-release of v1.48.0 after the GitHub Actions trigger issue.
- No functional code changes.
- Release-only bump to verify CI and PyPI publishing.

---

## [1.48.0] - 2026-08-06

### Changed
- **SQLiteMerkleTree.get_children_hashes now fetches all 16 nibble children in a single query** instead of 16 sequential `_get_hash` calls. This reduces `update_merkle_path` from up to 1,024 SQL statements per inserted object to 64, matching the existing Postgres implementation.
- **GossipFilter seen-set eviction is now driven by sequence number value, not insertion order.** Previously an insertion-order LRU could evict a legitimate in-window sequence number because it arrived first, letting a later replay of that value slip through as new. Now values are pruned only when they genuinely fall behind the reorder window, preserving the documented guarantee that no sequence number is ever accepted twice.

### Added
- New regression tests: `test_get_children_hashes_uses_one_query_not_sixteen`, `test_get_children_hashes_pattern_does_not_leak_across_prefixes`, and `test_replay_of_an_in_window_seq_no_is_rejected_regardless_of_arrival_order`.

---

## [1.47.0] - 2026-08-06

### Fixed
- **CRDT quarantine now actually wired into the gossip path** (ADR‑013). Previously `CRDTQuarantine` was implemented but never called by `GossipAdapter._merge_crdt_objects` — incoming objects from remote peers bypassed structural validation and identity checks entirely. Now every object admitted into the CRDT store goes through `quarantine.process_batch`, which runs `cks.validate()` on a properly constructed `KnowledgeStructure` and verifies that the object's recomputed SHA‑256 hash matches its claimed id. A single invalid object in a batch no longer aborts the whole merge.
- **`object_id_for()` no longer trusts bare `dict` payloads.** For live `cks.KnowledgeObject` instances the leaf hash was already used; for plain `dict`s (the typical deserialised gossip payload) the function now recomputes the hash from `identity`/`structure` and raises `ObjectIdentityMismatch` on mismatch, instead of blindly trusting a caller‑supplied `"id"` field.
- **`GossipFilter._check_sequence` now uses a sliding window** instead of strict monotonic ordering, so a legitimate message that arrives slightly out‑of‑order under concurrent gossip rounds is no longer permanently dropped.
- **`SeqNoCounter.next()` now guards its read‑increment‑write with a cross‑process file lock** (`fcntl.flock` / `portalocker`), preventing duplicate sequence numbers when two OS processes share the same `CKS_RUNTIME_DATA_DIR`.

### Changed
- `merge_objects` in all three CRDT‑store backends now logs and skips individual objects that raise `ObjectIdentityMismatch`, instead of failing the entire batch.

---

## [1.46.0] - 2026-08-06

### Added
- **CRDT adapter – Stage 2: MV‑Register, fork detection, and Conflict Event bus (ADR‑013).** The `cks_runtime/crdt/` module now supports pointer management via a multi‑value register (`cks_mv_register`), causal ordering through `causality_check()` (replacing simple Last‑Write‑Wins), automatic fork detection when two nodes produce concurrent versions of the same pointer, and a transactional outbox table (`cks_conflict_events`) that records conflicts for later resolution. PostgreSQL backends additionally emit `NOTIFY cks_fork_detected` so that listeners can react immediately.
- **`CRDTForkDetected`** runtime event – published on the `EventBus` whenever a gossip merge discovers a concurrent fork, carrying the pointer key, conflicting object ids, and the conflict event id.
- **`CRDTQuarantine`** (`cks_runtime/crdt/quarantine.py`) – validates incoming `KnowledgeObject`s through `cks.validate()` and Merkle‑identity checks before adding them to the CRDT store.
- **`GossipAdapter._detect_and_handle_fork`** – wired into the existing CRDT merge path so that MV‑Register updates automatically escalate concurrent situations into conflict events.
- New unit tests: `tests/unit/crdt/test_causality.py`, `tests/unit/crdt/test_mv_register.py`, `tests/unit/crdt/test_fork_detection.py`, `tests/unit/crdt/test_quarantine.py`, and `tests/unit/gossip/test_gossip_fork_integration.py` (239 tests total, all passing).

---

## [1.45.0] - 2026-08-06

### Added
- **CRDT adapter – Stage 1: G-Set + Merkle Tree (ADR-013).** A new `cks_runtime/crdt/` module that adds a conflict-free, grow-only set of `KnowledgeObject`s underneath the existing gossip transport, with a content-addressed Merkle prefix tree for efficient cross-node reconciliation.
  - `CRDTStore` – a G‑Set keyed by each object's own SHA‑256 leaf hash. Three backends: `SQLiteCRDTStore`, `PostgresCRDTStore` (async), and `InMemoryCRDTStore` (tests). Insertion is idempotent; two replicas that independently produce bit‑identical objects converge on one record automatically.
  - `MerkleTree` – a radix‑16 prefix tree over the 64‑hex‑character object ids. Inserting one object touches exactly 65 nodes (`update_merkle_path`); `get_root_hash` and `get_children_hashes` let gossip peers compare state cheaply. Order‑independent by construction — the same set of objects always produces the same root hash. PostgreSQL gets a PL/pgSQL trigger for automatic maintenance; SQLite recomputes in Python.
  - `VersionVector` (`cks_runtime/crdt/version_vector.py`) — a small, separate per‑node logical clock for CRDT replication progress, persisted in `cks_crdt_state`. Deliberately distinct from the ADR‑007 `VersionVector` used by `MergeOperation`.
  - **Gossip integration** — `GossipAdapter` now accepts an optional `crdt_store` and calls `_merge_crdt_objects` *before* the session‑level merge, guaranteeing every observed object is durably recorded even when the session reconciliation itself reports a conflict. Existing callers are unaffected (`crdt_store=None` by default).
- New ADR: `docs/adr/ADR-013 CRDT Adapter for Distributed Knowledge Objects.md`.
- New tests: `tests/unit/crdt/test_version_vector.py`, `tests/unit/crdt/test_merkle_tree.py`, `tests/unit/crdt/test_crdt_store.py`, and `tests/unit/gossip/test_gossip_crdt_integration.py`.

---

## [1.44.0] - 2026-08-06

### Added
- **`GraphHealthSweeper`** – background sweeper that computes an aggregate 0.0–1.0 health score for every registered graph, combining version freshness, TTL freshness, contradictions, verification coverage, and dead‑lettered conflict tasks. Escalates a `health_check` outbox task for any graph scoring below `graph_health_min_score` (default 0.7). Mirrors `GraphFreshnessSweeper`'s lifecycle, dedup strategy, and no‑op‑on‑unsupported‑storage behaviour.
- **`RuntimeConfig.graph_health_interval` / `graph_health_min_score`** – new configuration fields controlling the sweeper. Set `graph_health_interval=None` to disable.
- New unit tests in `tests/unit/reasoning/test_graph_health_sweeper.py` covering healthy graphs left untouched, stale+contradictory graphs escalated, custom thresholds, deduplication, re‑escalation after recovery, dead‑letter impact on score, and no‑op on `InMemoryStorage`.

---

## [1.43.0] - 2026-08-05

### Added
- **`last_error` field on `OutboxTask`** — `list_dead_letter_tasks()` in `SQLiteStorage` and `PostgresStorage` now populates the `last_error` field from the outbox row, so a reader (e.g. `cks-mcp`'s new `review_dead_letter` tool) can see why a task was dead-lettered without a separate query. `OutboxTask` dataclass gains an optional `last_error: str | None` field (default `None` for backward compatibility with existing callers that construct `OutboxTask` from other queries).

---

## [1.42.0] - 2026-08-05

### Added
- **`GraphAutoUpdateSweeper`** (Memory Agent v2) – background sweeper that cross‑checks every registered graph's `Component` objects' recorded `version` against the real `__version__` published in the matching GitHub repository (the same check `check_component_versions` performs on demand). Escalates a `graph_outdated` outbox task for each graph with at least one outdated component. Mirrors `GraphFreshnessSweeper`'s lifecycle, dedup strategy, and no‑op‑on‑unsupported‑storage behaviour.
- **`RuntimeConfig.graph_auto_update_interval` / `graph_auto_update_apply`** – new configuration fields controlling the sweeper. `graph_auto_update_apply` (default `False`) currently records `auto_apply_requested` on the escalated payload for a future cks‑mcp consumer (the actual update still requires `update_registered_graph` in cks‑mcp, consistent with Runtime never originating LLM calls or outbound HTTP beyond raw version fetches).
- **`cks_runtime.net.safe_fetch`** – SSRF/DNS‑rebinding‑safe outbound HTTP GET, ported from `cks_mcp.tools.verify_source.handler._safe_request`, so `GraphAutoUpdateSweeper` can fetch `_version.py` from raw.githubusercontent.com without opening an SSRF hole.
- New unit tests in `tests/unit/reasoning/test_graph_auto_update_sweeper.py` covering outdated and up‑to‑date components, `repo_url` resolution, deduplication, re‑escalation after regression, `auto_apply` flag propagation, GitHub‑unreachable resilience, and no‑op on `InMemoryStorage`.

---

## [1.41.0] - 2026-08-05

### Added
- **ADR-012 (Backup and Disaster Recovery)** – new ADR documenting the strategy for exporting/importing data between storage backends and for backup/restore workflows.
- **`export_storage()` and `import_storage(data, mode)`** – new methods on `RuntimeStorage`, `AsyncRuntimeStorage`, `InMemoryStorage`, `SQLiteStorage`, and `PostgresStorage`. `export_storage()` returns a complete dictionary dump of all sessions, versions, graphs, embeddings, and outbox tasks. `import_storage(data, mode)` restores data into the backend (`mode="clear"` wipes existing data first; `mode="merge"` adds to it). `SyncStorageAdapter` delegates calls to the sync backend.
- New tests in `tests/unit/storage/test_storage_export_import.py` (23 tests) cover round‑trip, merge/clear modes, and preservation of `graph_registry`, embeddings, and outbox tasks.

---

## [1.40.0] - 2026-08-05

### Added
- **`ContradictionSweeper`** – new background worker that proactively scans recently‑modified sessions for `MutualExclusionRule`/`FunctionalRelationRule` violations (the same `mutual_exclusion`/`functional_relation` constraints that `detect_contradictions` checks in cks‑mcp). Found contradictions are escalated as `contradiction_detected` tasks into the persistent outbox for the Critic Agent to resolve via `resolve_contradiction`. Runs automatically when `contradiction_sweep_interval` is not `None` (default: 1 hour); set it to `None` to disable.
- **`RuntimeConfig.contradiction_sweep_interval` / `contradiction_sweep_batch_size`** – new configuration fields controlling the sweeper.
- New unit tests in `tests/unit/reasoning/test_contradiction_sweeper.py` covering mutual‑exclusion and functional‑relation detection, no false positives, deduplication, re‑escalation after resolution, and no‑op on `InMemoryStorage`.

---

## [1.39.0] - 2026-08-04

### Added
- **`GraphFreshnessSweeper`** (Memory Agent v2) — background sweeper that walks the `graph_registry` table (Memory Agent v1) looking for entries whose `updated_at` has exceeded a TTL, and escalates a `graph_outdated` task into the persistent outbox for a future cks-mcp update agent to act on. Detection-only, same as `ProvenanceStalenessSweeper`/`TemporalStalenessSweeper`: does not refresh the graph or make any outbound HTTP requests itself. Mirrors those sweepers' lifecycle and wiring: configurable interval (`graph_freshness_interval`, default 1 hour) and TTL (`graph_freshness_ttl_seconds`, default 7 days); starts automatically with `Runtime` when `graph_freshness_interval` is not `None` (default: enabled); no-ops silently on storage backends without outbox support (e.g. `InMemoryStorage`). Unlike the session-content sweepers, it only requires `list_graphs` (implemented by every backend, including `InMemoryStorage`) rather than `list_sessions_modified_since`.
- **`RuntimeConfig.graph_freshness_interval` / `graph_freshness_ttl_seconds`** — new configuration fields controlling the sweeper. Set `graph_freshness_interval=None` to disable.
- **`public` field on `graph_registry`** — new boolean column (default `false`) in `SQLiteStorage`, `PostgresStorage`, and `InMemoryStorage`, migrated in-place for existing databases so every pre-existing registered graph stays private. `register_graph` accepts an optional `public: bool = False` parameter; `list_graphs` accepts an optional `public_only: bool = False` filter. This is the storage foundation for the Memory Agent gallery in `cks-mcp`.
- New unit tests in `tests/unit/reasoning/test_graph_freshness_sweeper.py` covering: detection of outdated graphs, fresh graphs left untouched, custom TTLs, malformed `updated_at` skipped without crashing, outbox task enqueued with correct `task_type="graph_outdated"`, deduplication and re-escalation across sweeps, no sweeper-side mutation of the registry, and no-op on unsupported storage.
- New unit tests in `tests/unit/storage/test_graph_registry.py` covering the `public` field's default, round-trip, `list_graphs(public_only=...)` filtering (alone and combined with `tag`), and backward compatibility with pre-existing v1 SQLite databases.

---

## [1.38.0] - 2026-08-04

### Added
- **Graph registry (`graph_registry`)** – new table in `SQLiteStorage`, `PostgresStorage`, and `InMemoryStorage` for persisting named session references (`name → session_id`). Methods `register_graph`, `get_graph`, and `list_graphs` allow registering knowledge graphs under memorable names, looking them up by name, and filtering by tag. This is the storage foundation for Memory Agent v1 in `cks-mcp`, which will let LLMs reuse graphs across conversations without rebuilding them from scratch.
- Abstract no-op defaults added to `RuntimeStorage` and `AsyncRuntimeStorage`, with delegation through `SyncStorageAdapter`.

---

## [1.37.0] - 2026-08-04

### Added
- **`TemporalStalenessSweeper`** (ADR-011) — background sweeper that detects objects whose `valid_until` has passed (via cks-core's opt-in `TemporalValidityConstraint`, ADR-003) and escalates them as `temporal_conflict` tasks into the persistent outbox for the Critic Agent to resolve. Mirrors `ProvenanceStalenessSweeper` (ADR-010) in lifecycle and wiring: configurable sweep interval (`temporal_sweep_interval`, default 1 hour); starts automatically with `Runtime` when `temporal_sweep_interval` is not `None` (default: enabled); no-ops silently on storage backends without outbox support (e.g. `InMemoryStorage`).
- **`RuntimeConfig.temporal_sweep_interval` / `temporal_sweep_batch_size`** — new configuration fields controlling the sweeper. Set `temporal_sweep_interval=None` to disable.
- New unit tests in `tests/unit/reasoning/test_temporal_staleness_sweeper.py` covering: detection of expired facts, fresh facts left untouched, malformed `valid_until` skipped without crashing, outbox task enqueued with correct `task_type="temporal_conflict"`, deduplication across sweeps, and no-op on unsupported storage.
- New `docs/adr/ADR-011 Temporal Staleness Detection.md` documenting the design.

### Requires
- `cks-core >= 1.20.0` for `cks.constraints.temporal.TemporalValidityConstraint`.

---

## [1.36.0] - 2026-08-04

### Added
- **`ProvenanceStalenessSweeper`** (ADR-010) — background sweeper that detects expired `VerificationRecord` objects and escalates them as `provenance_conflict` tasks into the persistent outbox for the Critic Agent to resolve. Mirrors `InferenceStalenessSweeper` (ADR-009) in lifecycle and wiring: configurable TTL (`provenance_ttl_seconds`, default 30 days) and sweep interval (`provenance_sweep_interval`, default 1 hour); starts automatically with `Runtime` when `provenance_sweep_interval` is not `None` (default: enabled); no-ops silently on storage backends without outbox support (e.g. `InMemoryStorage`).
- **`RuntimeConfig.provenance_sweep_interval` / `provenance_ttl_seconds` / `provenance_sweep_batch_size`** — new configuration fields controlling the sweeper. Set `provenance_sweep_interval=None` to disable.
- New unit tests in `tests/unit/reasoning/test_provenance_staleness_sweeper.py` covering: detection of expired records, fresh records left untouched, outbox task enqueued with correct `task_type="provenance_conflict"`, deduplication across sweeps, and no-op on unsupported storage.

---

## [1.35.0] - 2026-08-04

### Added
- **`touch_outbox_task`** — a new method on `RuntimeStorage`, `AsyncRuntimeStorage`, `SQLiteStorage`, and `PostgresStorage`. Allows a worker that is still actively processing a task (e.g., a Critic Agent waiting for an LLM response) to update `claimed_at` and prevent the task from being mistakenly claimed by another worker after the standard lease timeout expires. The `SyncStorageAdapter` proxies calls to the sync storage. For `InMemoryStorage`, returns `False` (no-op).
- New tests in `tests/unit/storage/test_sqlite_storage.py` and `tests/unit/storage/test_postgres_storage_mocked.py` cover successful updates, protection against re-claiming, returning `False` for non-existent/completed tasks, and the case where the task has already been claimed by another worker.

---

## [1.34.2] - 2026-08-03

### Fixed
- **`OutboxEmbeddingWorker._process_next_task`** no longer dequeues an untyped task and branches on `task.task_type == "projection"` -- it now calls `dequeue_next_outbox_task(task_type="projection")` (added in 1.34.0), so any other task type sharing `cks_outbox_tasks` (e.g. a Critic agent's `"gossip_conflict"`/`"inference_conflict"` tasks) is never claimed, and can never be routed into this worker's retry/backoff loop with `ValueError: Unknown task type`. This was the concrete blocker 1.34.0/1.34.1 added the filtering capability for, but the worker itself hadn't been switched over yet.
- New regression tests in `tests/unit/projection/test_embedding_projection.py` covering: a foreign task type is left untouched (not claimed, not failed) when the worker runs, and the worker still reaches its own `"projection"` task when a foreign-typed task is queued ahead of it.

---

## [1.34.1] - 2026-08-03

### Added
- **Conformance tests for outbox task_type filter, DLQ, and batch-by-type methods** — new test cases in `tests/conformance/test_storage_conformance.py` cover `dequeue_next_outbox_task` with `task_type`, `dead_letter_outbox_task`, `list_tasks_by_type` (including `session_id` filter and `drain` behaviour), and `list_dead_letter_tasks`. Tests run against SQLite (12 scenarios) and InMemoryStorage (no-op paths); Postgres skipped when no `CKS_TEST_POSTGRES_DSN` is set. No functional changes to the storage layer itself.

---

## [1.34.0] - 2026-08-03

### Added
- **`task_type` filter in `dequeue_next_outbox_task`** – storage backends now accept an optional `task_type` parameter to restrict task claiming to a specific type (e.g. `"projection"`, `"gossip_conflict"`, `"inference_conflict"`). This allows multiple workers sharing one outbox table to never steal each other's tasks. Implemented for SQLiteStorage and PostgresStorage; InMemoryStorage returns None (no-op).
- **`dead_letter_outbox_task`** – permanently marks a task as DEAD after repeated failures, removing it from the eligible pool while preserving it for inspection via `list_dead_letter_tasks`. Mirrors the existing `fail_outbox_task` but without scheduling a retry.
- **`list_tasks_by_type`** – batch peek/drain read over PENDING tasks of a given type, with optional `session_id` filtering and `drain` control. Provides the outbox-backed equivalent of `ConflictInbox.list` / `list_inference` for gossip and inference conflicts. Implemented for SQLiteStorage and PostgresStorage.
- **`list_dead_letter_tasks`** – returns every DEAD-lettered task (optionally filtered by type) for operator inspection. Never drains.

### Changed
- Abstract interfaces `RuntimeStorage` and `AsyncRuntimeStorage` now declare the new methods with default no-op implementations, preserving full backward compatibility with existing backends.

---

## [1.33.0] - 2026-08-03

### Added
- **`InferenceStalenessSweeper`** (ADR-009) – new background process that periodically scans sessions for stale or conflicting inference steps (`InferenceStep`). Uses `list_sessions_modified_since` to incrementally fetch candidates and validates them with `inference_confidence_conflict` and `stale_premise` constraints. Publishes `InferenceConflictDetected` on the `EventBus` for newly discovered issues while suppressing duplicates. Configured via `RuntimeConfig.inference_sweep_interval` (disabled by default).
- **`InferenceConflictDetected`** – new `RuntimeEvent` carrying `session_id`, `version_id`, and `diagnostics` to notify subscribers about detected reasoning conflicts.
- **`RuntimeStorage.list_sessions_modified_since`** – new method on `RuntimeStorage` and `AsyncRuntimeStorage` that returns sessions modified on or after a given watermark. Implemented for `SQLiteStorage` and `PostgresStorage`; `InMemoryStorage` returns an empty list.
- Added `inference_sweep_interval` and `inference_sweep_batch_size` to `RuntimeConfig` for controlling the sweeper.
- `InferenceStalenessSweeper` exported at the package level, mirroring the existing `GarbageCollector` export.

### Changed
- `Runtime.__init__`, `Runtime.create()`, and `Runtime.aclose()` now manage the lifecycle of `InferenceStalenessSweeper` when enabled.

---

## [1.32.0] - 2026-08-02

### Added
- **`Runtime.register_foreign_branch`** — new public method that registers a branch of a parent session whose content originates from outside this Runtime (e.g. a remote replica's snapshot received via gossip). Unlike `create_branch`, which only ever forks the parent's own content, this method accepts a caller-supplied `KnowledgeStructure` together with an explicit `parent_version_id` and optional `metadata`. Returns a fully-addressable `RuntimeSession` ready for `merge_branch` / `compare_versions` / `explain_diff`, and publishes `SessionCreated` for the new branch, consistent with `create_session`/`create_branch`.
- **`GossipConflictDetected.source_session_id`** — the event now carries the `session_id` of a local branch materialized from the remote content that failed to merge (via `register_foreign_branch`). A subscriber (e.g. a Critic agent) can pass `target_session_id=session_id, source_session_id=source_session_id` straight to `merge_branch` — or diff against it first — instead of having only a bare list of conflicting object ids with no way to see what the remote side actually contained. Empty when branch registration itself failed (defensive: an empty id is still an escalation, better than losing the event entirely).

### Changed
- **Gossip conflict deduplication** — `GossipAdapter` now tracks which remote `VersionVector` most recently triggered an *unresolved* conflict for each `session_id`. A gossip round that re-sends the same unresolved content (e.g. a scheduled retry before the conflict is addressed) does not leak a second `RuntimeSession` into the registry or re-publish `GossipConflictDetected` for content a subscriber already knows about. The guard is cleared whenever that `session_id` is resolved by any path (dominated, fast-forwarded, or merged), so a genuinely new divergence after resolution is still escalated fresh.

---

## [1.31.2] - 2026-08-02

### Fixed
- **`GossipConflictDetected` now carries `session_id`.** `GossipAdapter` previously published this event with only `source_replica_id` and the conflicting object ids — a subscriber (e.g. a Critic agent listening on the `EventBus`) had no way to tell *which* gossiped session conflicted, and so no way to pass the right session to `merge_branch`/`compare_versions` when more than one session is gossiping concurrently. `_apply_remote_session_locked` now passes `session_id=local.session_id` at the point it escalates. See SPEC-009 Section 8.

---

## [1.31.1] - 2026-08-02

### Changed
- Bumped `cks-core` dependency floor to `>=1.19.0` — absorbs the new `ResolveInferenceConflict` structural operator (the write-side counterpart to `InferenceConfidenceConflictConstraint`). No code changes in cks-runtime itself.

---

## [1.31.0] - 2026-08-02

### Added
- **`CoreInterface.explain_inference()` / `CoreBridge.explain_inference()`** – new *optional* Core capability, mirroring `field_diff()`/`synthesize_merge()`: raises `NotImplementedError` by default, so Runtime never assumes every plugged-in Core has a "reasoning objects" vocabulary to walk. `CoreBridge.supports_explain_inference` mirrors `supports_field_diff`.
- **`CksCoreAdapter.explain_inference()`** – implements the new capability by delegating to `cks.constraints.reasoning.explain_inference` (cks-core 1.18.0), answering "why is this object currently believed?" by walking its active `InferenceStep` chain(s).
- **`ExplainInferenceOperation`** – new read-only Runtime Operation, mirroring `QuerySubgraphOperation`'s error-handling convention (broad `except Exception`, including the optional capability's `NotImplementedError`, turned into a `FAILED` `ExecutionResult` rather than propagated). Requires `object_id`; not special-cased in `ExecutionPipeline._apply_state_mutation`, same as `ExplainOperation`/`QuerySubgraphOperation`.

### Changed
- `cks-core` dependency floor raised to `>=1.18.0` for `cks.constraints.reasoning.explain_inference`.

---

## [1.30.2] - 2026-08-02

### Fixed
- **`Runtime.create_session`/`create_branch`/`close_session` never published `SessionCreated`/`SessionClosed` events.** Both event types existed on the EventBus but were never fired, so lifecycle logging in `cks-mcp` and any other subscribers silently saw no session events. All three methods now publish the corresponding event after persisting.

---

## [1.30.1] - 2026-08-02

### Fixed
- **Race in `GossipAdapter.apply_remote_session` for concurrent inbound gossip** – two inbound gossip requests for the same `session_id` arriving concurrently (the normal shape of load on a multi-peer mesh, surfaced by the demo's real HTTP concurrency rather than sequential manual calls) could both pass `TransactionManager.begin`'s "no active transaction yet" check before either committed, and the second would raise `RuntimeError("Session already has an active transaction.")`. Not data-corrupting – the losing round was simply dropped and the next round still converged – but noisy and a wasted round under real concurrent load. `apply_remote_session` is now serialized per `session_id` via a lazily-created `asyncio.Lock` (`GossipAdapter._lock_for`); unrelated sessions are unaffected and still reconcile fully concurrently.

---

## [1.30.0] - 2026-08-02

### Added
- **Genesis Block for gossip convergence** – `EMPTY_STATE_VERSION_ID` (`"00000000-0000-0000-0000-000000000000"`) allows two independently bootstrapped replicas to name a shared common ancestor for three-way merge. `MergeOperation` resolves this constant to an empty structure without requiring a real version in any session's history.
- **`GossipAdapter.anchor_genesis(session)`** – anchors a locally-created session to `EMPTY_STATE_VERSION_ID`, giving it the same fork point that gossip-bootstrapped peers get automatically.
- **`_bootstrap_remote_session`** now always sets `parent_version_id = EMPTY_STATE_VERSION_ID` on the adopted local copy.
- **End-to-end convergence test** – `TestThreeReplicaConvergenceViaGenesis` reproduces the Supervisor/Critic/Worker scenario with real bootstrap and concurrent field-disjoint edits, confirming convergence within a handful of rounds without escalated conflicts.

---

## [1.29.0] - 2026-08-02

### Added
- **`VersionVector` now bumped by `replica_id`** – `VersionManager.create()` accepts an optional `replica_id` parameter (ADR-008 §1). When supplied, the version vector is bumped for both `node_id` and `replica_id`, giving gossip peers a durable, cross-restart identity to anchor anti-entropy comparisons.
- **`Runtime.replica_id` property** – sourced once from `storage.get_or_create_replica_id()` during `Runtime.create()`, exposed as a public property. `None` for bare `Runtime(...)` instances or backends without gossip support.
- **`ExecutionPipeline._create_version`** now passes `replica_id` from the runtime to `VersionManager.create()`.

### Changed
- ADR-008 updated with a revision note documenting the `replica_id` bump and its scope: it closes the durable identity problem but does not yet solve convergence for independently bootstrapped sessions without shared lineage.

### Fixed
- `VersionManager.create()` no longer ignores `replica_id` when `node_id` is also present – both keys are bumped together.

---

## [1.28.1] - 2026-08-01

### Fixed
- **Shared `seq_no` counter for gossip** – `GossipService` and `GossipServer` no longer maintain independent sequence-number counters for the same `replica_id`, which previously caused legitimate bidirectional messages to be rejected as replays. A single `SeqNoCounter` is now shared between both components.
- **`seq_no` persists across restarts** – `SeqNoCounter` stores the last sequence number on disk (same directory as the gossip secret), so a restarted replica continues monotonically instead of resetting to 0 and being permanently rejected by peers.
- **Test isolation for gossip persisted state** – a new `conftest.py` fixture redirects `CKS_RUNTIME_DATA_DIR` to a temporary directory during tests, preventing accidental writes to `~/.cks_runtime`.

---

## [1.28.0] - 2026-08-01

### Added
- **Integration HTTP tests for gossip** – 14 new tests in `test_http_transport.py` covering fast-forward, three-way merge, bootstrap of unknown sessions, signature rejection, replay-filter handling over real HTTP, peer discovery via `/gossip/peers`, and a full `GossipService` round.
- **Peer discovery on `GossipService` start** – `GossipService.start()` now calls `discover_peers()` to query all known peers for additional addresses before entering the background loop.
- **SPEC-009_Gossip_Replication.md** – formal specification for the gossip envelope format, `apply_remote_session` semantics, and the `GossipTransport` contract for third-party transports.

### Changed
- `GossipService` now has a `discover_peers()` method and performs an initial discovery sweep on start.
- `test_http_transport.py` — previously referenced in docstrings but non-existent; now fully implemented and passing.

---

## [1.27.2] - 2026-08-01

### Added
- **Bootstrap of unknown sessions** – `GossipAdapter.apply_remote_session` now accepts remote sessions that have no local counterpart yet. The remote state is deep-copied into a new `RuntimeSession`, registered, and persisted with a fresh `node_id`.
- **Peer discovery** – new `discovery.py` module with a `PeerDiscovery` abstract protocol and `HTTPPeerDiscovery` in `http_transport.py`. `GossipServer` exposes a `/gossip/peers` endpoint; `GossipService` merges discovered peers into the `PeerScheduler` after each successful gossip round.

### Fixed
- `apply_remote_session` no longer returns `False` for unknown sessions – they are now bootstrapped and accepted.
- `test_unknown_local_session_returns_false` replaced with four new tests covering bootstrap behaviour, structurally-equivalent snapshots, and error handling.

---

## [1.27.1] - 2026-08-01

### Fixed
- **Gossip transport tests** – all three previously skipped tests (`test_converges_two_replicas_that_each_committed`, `test_returns_true_when_peer_has_nothing_new`, `test_replay_filter_rejects_a_resent_reply`) now pass. `test_converges_two_replicas_that_each_committed` rewritten to use a symmetric shared-base helper so the merge base is resolvable in both directions.
- **`apply_remote_session`** – added `structurally_equivalent` fast-path: when local and remote snapshots are already identical (same root hash), the method returns `True` immediately instead of escalating a spurious conflict.
- **`test_gossip_envelope.py`** – fixed mutable default argument (`list[str] = ["root"]`) in `make_session` helper.

---

## [1.27.0] - 2026-08-01

### Added
- **Gossip transport layer** – new `cks_runtime/gossip/` package implementing the peer-to-peer exchange for ADR-008:
  - `GossipEnvelope` – HMAC-SHA256 signed session snapshots for secure, verifiable gossip messages.
  - `GossipFilter` – replay protection, sequence-number validation, and clock-skew detection for incoming gossip envelopes.
  - `GossipTransport` – abstract protocol for sending/receiving gossip envelopes between replicas.
  - `HTTPGossipTransport` – reference implementation of `GossipTransport` using `aiohttp` (client + server).
  - `PeerScheduler` – weighted peer selection with exponential backoff for handling network failures.
  - `GossipService` – background `asyncio.Task` that ties together the scheduler, transport, and adapter for periodic anti-entropy cycles.
  - `secret.py` – dedicated HMAC secret management for gossip, separate from `cks-mcp`'s provenance secret.
- **Optional `gossip` extra** – `pip install cks-runtime[gossip]` installs `aiohttp` for the HTTP transport.

### Fixed
- `_paired_replicas` test helper now correctly initialises `node_id` on hand-built sessions, fixing `VersionVector` comparison in gossip tests.
- `test_real_merge_conflict_is_escalated_not_raised` rewritten to use two-replica setup with a genuine common ancestor, actually exercising the `RuntimeMergeConflictError` path.

### Changed
- ADR-008 status updated from `Proposed` to `Partially Implemented`, reflecting the session-snapshot gossip design and transport layer.

---

## [1.26.2] - 2026-08-01

### Fixed
- **`_paired_replicas` test helper** – hand-built `RuntimeSession` for the "second replica" was missing `metadata["node_id"]`, so `ExecutionPipeline._persist` silently skipped `VersionVector.bump()` on every commit; the replica's vector stayed permanently empty, breaking `dominates()`/fast-forward comparisons in `GossipAdapter`.
- **`test_gossip_adapter.py`** – all 4 previously-`skip`-marked gossip tests (fast-forward, no-common-ancestor escalation, real field-level conflict escalation, full exchange convergence) now pass; none skipped. `test_real_merge_conflict_is_escalated_not_raised` was rewritten around a new `_paired_replicas_with_shared_base` helper — the old version passed a `create_branch` result straight into `apply_remote_session`, but a branch always mints its own `session_id`, so `apply_remote_session`'s `get_session(remote_session.session_id)` lookup resolved "local" back to the branch itself (a trivial self-comparison) and never reached a real merge.

### Changed
- **ADR-008** – status note updated to reflect the v1.26.1 `GossipAdapter` rewrite (session-snapshot exchange via `MergeOperation`, not operation-log replay); top-level status moved from `Proposed` to `Partially Implemented`; stale reference to a never-implemented `apply_remote_operations` storage method removed from Consequences.

---

## [1.26.1] - 2026-08-01

### Added
- **`get_or_create_replica_id`** – durable per-process identity for gossip, persisted via new `cks_runtime_identity` table in SQLiteStorage and PostgresStorage. InMemoryStorage provides an auto-generated transient replica_id for testing.
- **`fetch_operations_since`** – new method on `RuntimeStorage` / `AsyncRuntimeStorage` for retrieving operation-log entries not yet reflected in a given `VersionVector`.
- **`list_operations` `version_id` filter** – `list_operations` now accepts an optional `version_id` parameter for scoped queries (SQLite, Postgres, InMemory).

### Fixed
- **`SyncStorageAdapter`** now properly forwards `list_operations` with the new `version_id` parameter, and exposes `get_or_create_replica_id` / `fetch_operations_since`.
- **Duplicate `GossipConflictDetected`** class removed; canonical event is now only in `events/runtime_event.py`.
- **`GossipAdapter` rewritten** to exchange full `RuntimeSession` snapshots via the existing `MergeOperation` path (probe → commit), replacing the non-functional field-operation-replay design. (ADR-008)

---

## [1.26.0] - 2026-08-01

### Added
- **`GossipAdapter`** – applies remote operations from another replica, using the storage layer's `fetch_operations_since` and the existing `MergeOperation`. (ADR-008)
- **`GossipConflictDetected` event** – published when a merge conflict is detected during gossip.
- **`gossip_exchange()`** – in-process helper to exchange operations between two replicas for testing.

---

## [1.25.2] - 2026-07-31

### Changed
- Bumped `cks-core` dependency to `>=1.16.0` (inference confidence conflict constraint, RecordInference operator).

---

## [1.25.1] - 2026-07-31

### Fixed
- **Postgres `executemany`** – `record_operations` now calls `cursor.executemany` instead of the non-existent `connection.executemany`.
- **Public properties in `patch_codec`** – switched from `cks-core` private attributes to public operator properties (`.obj`, `.object_id`).
- **`zip(..., strict=True)` in `outbox_worker`** – prevents silent data loss when the list of texts and embeddings diverge.
- **`snapshot_interval` validation** – `RuntimeSession` now enforces a positive interval.
- **Graceful failure in `Dispatcher`** – malformed parameters no longer crash the dispatcher; a readable error is returned instead.

### Changed
- **Single version source** – `pyproject.toml`, `config.py`, and `CITATION.cff` are now synchronised through `_version.py`.
- **CI with real PostgreSQL** – Postgres integration tests are no longer skipped; a full Postgres + pgvector service is spun up in CI.

---

## [1.25.0] - 2026-07-31

### Added
- **`FastEmbedEmbeddingClient`** – local, token-free embedding client backed by fastembed (ONNX Runtime). No API key or network calls required. Opt-in via `pip install cks-runtime[fastembed]`.

---

## [1.24.0] - 2026-07-30

### Added
- **Session Garbage Collector** – background `asyncio.Task` that periodically evicts stale closed sessions from storage, keeping the active sessions table compact in long-running deployments. Configured via `RuntimeConfig.gc_retention` (default 24h), `gc_sweep_interval` (default 10 min), and `gc_batch_size` (default 100). Open sessions are never evicted.
- **GC storage methods** for both SQLite and Postgres: `list_sessions_modified_before` and `archive_session`. Archived sessions are moved to an `archive_sessions` table for audit purposes.
- **`modified_at` column** on the `sessions` table, automatically maintained by `save_session`. Online migration for existing databases.
- **`GarbageCollector`** exported from `cks_runtime` top-level package.

### Changed
- `Runtime.create()` now starts the GC background task (when enabled).
- `Runtime.aclose()` now stops the GC background task gracefully.

---

## [1.23.1] - 2026-07-30

### Changed
- `OutboxEmbeddingWorker` now uses public properties (`op.object_id`) instead of private attributes, compatible with `cks-core>=1.14.0`.
- Bumped `cks-core` dependency to `>=1.14.0`.

---

## [1.23.0] - 2026-07-30

### Added
- **PostgreSQL storage backend** (`PostgresStorage`, async) — sessions, versions, outbox (with `SELECT ... FOR UPDATE SKIP LOCKED`), operation log, and pgvector-based embeddings with HNSW index.
- **Outbox support** for Postgres — background task processing via `cks_outbox_tasks` table with atomic claim, retry with exponential backoff, and stale lease recovery.
- **pgvector embeddings** — native `vector` type with HNSW index for sub-millisecond similarity search on millions of rows. Dimension is auto-detected and validated on every insert.
- **`patch_codec` module** — shared serialization/deserialization of structural operators (patches) used by both SQLite and Postgres backends.
- **`AsyncRuntimeStorage` enhancements** — added `search_embeddings`, `supports_embedding_search`, `list_operations` to the async interface.
- **`SyncStorageAdapter`** now proxies `search_embeddings` via `asyncio.to_thread`.

### Fixed
- **BUG-01 (data loss):** SQLite `cks_object_embeddings` table now uses composite primary key `(object_id, session_id)` instead of `object_id` alone, preventing cross-session embedding overwrites. Safe migration for existing databases included.
- **BUG-03 (N+1 queries):** `SQLiteStorage.list_sessions` now loads all sessions and versions in a single LEFT JOIN query instead of one query per session.
- **BUG-05 (version index):** Temporarily reverted `_version_id_index` cache in `RuntimeSession` due to slots incompatibility; linear search retained. Proper O(1) lookup will be reintroduced in a future release.
- **OPT-01:** `InMemoryStorage.list_sessions` no longer creates a temporary tuple just for deepcopy.

### Known Gap
- `PostgresStorage` is not yet wireable into the synchronous `Runtime`; a bridge (async-native `Runtime` or sync adapter) is planned as follow‑up work. Use `await Runtime.create(...)` with a `postgres://` DSN for full functionality.

---

## [1.22.1] - 2026-07-29

### Fixed
- `SyncStorageAdapter.search_embeddings` — the 1.22.0 async migration left
  `search_embeddings` (and a `supports_embedding_search` flag) off both
  `AsyncRuntimeStorage` and `SyncStorageAdapter`. Callers that check
  `hasattr(runtime.storage, "search_embeddings")` (e.g. cks-mcp's
  `search_semantic`) would silently stop finding the method once
  `runtime.storage` became a `SyncStorageAdapter` wrapping a
  `SQLiteStorage`. Both are now present: `AsyncRuntimeStorage` gets an
  empty-by-default `search_embeddings` plus `supports_embedding_search`
  (same optional-subsystem convention as outbox/operation-log), and
  `SyncStorageAdapter` dispatches to the wrapped sync backend's
  `search_embeddings` via `asyncio.to_thread` when the backend supports
  it.

---

## [1.22.0] - 2026-07-29

### Added
- **Full async runtime:** `Runtime`, `ExecutionPipeline`, `OperationExecutor`, `Dispatcher`, and all operations are now async end-to-end. Sessions, transactions, and versions are persisted via `await`.
- **`PostgresStorage`** — production-grade PostgreSQL backend with connection pooling, JSONB storage, and CAS concurrency control.
- **`AsyncRuntimeStorage` ABC** — async counterpart of `RuntimeStorage` for network databases.
- **`SyncStorageAdapter`** — transparently wraps `InMemoryStorage`/`SQLiteStorage` for async runtime, using `asyncio.to_thread` to avoid blocking the event loop.
- **`EventBus`** now supports `async` handlers; publish is `await`-ed.
- **`OutboxEmbeddingWorker`** runs as an `asyncio.Task` instead of a thread.
- **Shared `patch_codec`** — operator serialization used by both SQLite and Postgres backends.

### Changed
- `Runtime` construction is split: `Runtime()` does synchronous wiring; `await Runtime.create()` performs async startup (restore sessions, start outbox worker, connect Postgres pool).
- `Operation.execute()` is now `async` across all subclasses.
- `SQLiteStorage` delegates operator serialization to `patch_codec`.

### Known Gap
- `cks-mcp` is still synchronous and uses `SyncStorageAdapter`; full async integration is planned as follow‑up work.

### Upgrade Notes
- All existing synchronous callers (including `cks-mcp`) continue to work unchanged — `SyncStorageAdapter` wraps sync backends automatically.
- Direct instantiation via `Runtime()` works without `await`, but sessions/versions won't be restored from storage at startup; use `await Runtime.create()` for full recovery.

---

## [1.21.0] - 2026-07-29

### Added
- **PostgreSQL storage backend** (`PostgresStorage`, async) — schema, session/version CRUD, CAS concurrency control using JSONB and `IS NOT DISTINCT FROM`. Requires `psycopg[binary,pool]>=3.1` (optional extra `postgres`).
- **`AsyncRuntimeStorage` ABC** — awaitable counterpart of `RuntimeStorage` for network databases.
- **`patch_codec`** — shared patch serialization used by both SQLite and Postgres backends, eliminating code duplication.
- **Conformance tests** for the Postgres backend (14 tests, including a real concurrent-CAS race via `asyncio.gather`). Skipped by default unless `CKS_TEST_POSTGRES_DSN` is set.

### Changed
- `SQLiteStorage` now uses `patch_codec` for operator serialization instead of its own private copies.

### Known Gap
- `PostgresStorage` is not yet wireable into the synchronous `Runtime`; a bridge (async-native `Runtime` or sync adapter) is planned as follow‑up work.

---

## [1.20.3] - 2026-07-29

### Changed
- Clarified `VersionVector.dominates()` docstring: empty vector never dominates anything, matching `MergeOperation.execute` semantics.
- Bumped `cks-core` dependency to `>=1.13.1`.

---

## [1.20.2] - 2026-07-28

### Changed
- Bumped `cks-core` dependency to `>=1.13.0` (contradiction detection: `mutual_exclusion` and `functional_relation` optional constraints).

---

## [1.20.1] - 2026-07-28

### Changed
- Bumped `cks-core` dependency to `>=1.12.1` (includes fix for truncated `schema.py` and new test coverage).
- Development status promoted to **Production/Stable** in PyPI classifiers.
- Updated internal fallback version to `1.20.1`.

---

## [1.20.0] - 2026-07-28

### Added
- **ADR-007 Part 2 — Version Vectors and Fast-Path Merge.**  
  - `VersionVector` class tracks per-session commit clocks, enabling `MergeOperation` to detect no-op and fast-forward scenarios without running a full three-way merge.  
  - `Runtime` now generates a unique `node_id` (uuid4) for each instance, used to increment the session's version vector on every commit via `VersionManager.create()`.  
  - `MergeOperation.execute()` checks vector dominance before resolving the merge base: if target dominates source → no-op; if source dominates target → fast-forward.  
- `node_id` added to `Runtime.__slots__` and `Runtime.__init__`.

---

## [1.19.0] - 2026-07-28

### Added
- **Index on `cks_object_embeddings.session_id`** — accelerates embedding lookups in multi-session scenarios (EXPLAIN QUERY PLAN confirms SEARCH... USING INDEX instead of SCAN).
- **NumPy-vectorized `search_embeddings`** — all candidate vectors are scored in a single matrix-vector product, yielding ~10× speedup on typical workloads (5000 objects). `numpy>=1.26` added as a runtime dependency.
- **`mypy.ini` with selective strict-mode** for 9 already-clean submodules (`core_api`, `diagnostics`, `dispatcher`, `events`, `execution`, `explainability`, `metrics`, `session`, `transaction`).
- 7 new tests for `search_embeddings` covering ranking, top_k, session isolation, dimension mismatch, and similarity clamping.

### Fixed
- All 21 mypy errors resolved: type narrowing in `CoreBridge` (16 occurrences), generic `EventBus.subscribe/unsubscribe`, `VersionManager` invariants, and `MergeOperation` private method assertion.
- One genuinely silent `except Exception` in `MergeOperation._field_level_resolutions` now logs the failure instead of swallowing it.
- PEP 695 generic function syntax (`[T]`) adopted for `_retry_on_locked` and `_retry_on_transient_hf_error`, replacing module-level `TypeVar`.

---

## [1.18.2] - 2026-07-27

### Changed
- Bumped `cks-core` dependency to `>=1.12.0` (ontology/type-hierarchy constraints, CKS-009 spec).

---

## [1.18.1] - 2026-07-27

### Fixed
- `RuntimeTransaction` now captures `session.knowledge_structure` at begin time; `ExecutionPipeline.rollback()` restores it, so a transaction with multiple operations that partially mutated the session is properly reverted on failure, not just marked as "rolled back".
- `DispatchRequest` path in `_execute_operations` now records results and applies state mutation via `result.operation`, mirroring the `transaction.operations` loop — previously, an `EvolveOperation`/`MergeOperation`/`RevertVersionOperation` dispatched through `add_request` would silently commit without updating the session's Knowledge Structure.
- `OperationExecutor` now centrally attaches the `Operation` instance to every `ExecutionResult`, making it available to both the direct and dispatch paths for `_apply_state_mutation`.

---

## [1.18.0] - 2026-07-27

### Added
- `SQLiteStorage.dequeue_next_outbox_task` now atomically claims a task (`status='IN_PROGRESS'`, `claimed_at` set) as part of the same `UPDATE ... RETURNING` statement that reads it, instead of a plain `SELECT`. Two workers polling the same outbox table concurrently (e.g. two `cks-mcp` server processes sharing a SQLite file) can no longer both dequeue and process the same task. Verified with a real multi-threaded, multi-connection test, not just sequential calls.
- A claimed task whose worker crashed or hung without calling `complete_outbox_task`/`fail_outbox_task` is automatically reclaimed by another `dequeue_next_outbox_task` call once its lease goes stale (5 minutes), so a dead worker can no longer strand a task in `IN_PROGRESS` forever.
- New `claimed_at` column on `cks_outbox_tasks` (migrated in for existing databases). `fail_outbox_task` clears it when resetting a task back to `PENDING`.
- `HuggingFaceEmbeddingClient.embed_batch` now passes an explicit 30s `timeout` to the underlying HTTP call (previously unbounded — a hung Hugging Face Inference API request would block the caller indefinitely) and retries transient failures (connection errors, timeouts, HTTP 429/5xx) with exponential backoff, up to 3 attempts. Non-retryable errors (4xx other than 429 — bad model name, malformed payload, invalid token) are raised immediately.
- New tests: `tests/unit/embedding/test_client.py` (7 tests covering timeout, retry, and non-retry behavior) and 6 new outbox tests in `tests/unit/storage/test_sqlite_storage.py`, including a real multi-threaded concurrency regression test.

### Fixed
- Removed a redundant local `import json` inside `OutboxEmbeddingWorker._process_next_task` — `json` was already imported at module level (leftover from the 1.17.5 JSON-parsing fix).
- Fixed a `mypy` `no-redef` error in `HuggingFaceEmbeddingClient.__init__` where `self._dimension`'s type was re-annotated in an `else` branch.

---

## [1.17.7] - 2026-07-26

### Changed
- `SQLiteStorage.search_embeddings` now returns `list[tuple[object_id, similarity_score]]` instead of a bare `list[object_id]`. `similarity_score` is the cosine similarity between the query and stored vectors (a plain dot product, since both are normalized), clamped to `[0.0, 1.0]` so a negative raw similarity doesn't leak out as a meaningless "less than least similar" value. Results are still ordered most-to-least similar. **Breaking change** for any direct caller of `search_embeddings`; `cks-mcp`'s `search_semantic` has been updated accordingly (see `cks-mcp` 1.7.14).

---

## [1.17.6] - 2026-07-26

### Fixed
- `HuggingFaceEmbeddingClient` now detects embedding dimension dynamically from the API response instead of hardcoding 384. Supports `CKS_EMBEDDING_MODEL` and `CKS_EMBEDDING_DIMENSION` environment variables for configuration.

---

## [1.17.5] - 2026-07-26

### Fixed
- `OutboxEmbeddingWorker` now correctly parses JSON payload when processing projection tasks, fixing a crash that prevented any embeddings from being generated.

---

## [1.17.4] - 2026-07-26

### Fixed
- `SQLiteStorage.search_embeddings` now safely skips stored embeddings whose dimension doesn't match the query vector, instead of silently computing garbage similarity scores.
- `Runtime` now properly stores and exposes the configured `embedding_client` via a public property, ensuring query-time and index-time use the same client instance.

---

## [1.17.3] - 2026-07-26

### Fixed
- `Runtime.close_session()` now persists the closed state to storage, preventing closed sessions from resurrecting as active after a process restart.
- `SessionManager.restore()` no longer re-registers closed sessions loaded from storage, ensuring they stay unreachable via `get_session()`.
- Added regression test `test_closed_session_stays_closed_after_runtime_restart`.

---

## [1.17.2] - 2026-07-26

### Fixed
- `OperationExecutor.execute()` now accepts `record_metrics=False` to suppress double-counting of probe-only executions in `get_metrics`. Probe calls from `evolve_knowledge` and `merge_branch` are now unmetered.
- Resolved PyPI upload conflict with v1.17.0.

---

## [1.17.1] - 2026-07-26

### Fixed
- `OperationExecutor.execute()` now accepts `record_metrics=False` to suppress double-counting of probe-only executions in `get_metrics`. Probe calls from `evolve_knowledge` and `merge_branch` are now unmetered.

---

## [1.17.0] - 2026-07-26

### Added
- **Field-level auto-merge for disjoint edits (ADR-007 Part 2).**  
  - `MergeOperation` now automatically resolves conflicts when both branches only touched disjoint `structure` keys on the same object, using the operation log and `synthesize_merge`.  
  - `RuntimeFieldOperation.version_id` — identifies which committed version each logged operation belongs to, enabling scoped queries since a merge base.  
  - `CoreInterface.synthesize_merge()` / `CoreBridge.synthesize_merge()` — write-side counterpart to `field_diff`, constructing a merged object from non-conflicting field-level operations.  
  - `CksCoreAdapter.synthesize_merge()` — implements synthesis via `UpdateObject(mode="replace")` with explicit delete/set handling.  
  - `OperationExecutor` now exposes `storage` read-only, allowing `MergeOperation` to look up the operation log during merge probes.  
  - New tests for `synthesize_merge`, round-trip operation log with `version_id`, and field-level merge execution.

### Changed
- `RuntimeFieldOperation` now distinguishes `delete_field` from `set_field` with `None` value — resolving the ambiguity between "key deleted" and "key explicitly set to None".  
- `CksCoreAdapter.field_diff()` emits `delete_field` for removed keys instead of `set_field` with `None`.  
- `SQLiteStorage.list_operations()` returns `version_id` for every logged operation.  
- Updated integration test for `ExecutionPipeline` to use real `CksCoreAdapter` and `KnowledgeStructure`.

---

## [1.16.0] - 2026-07-26

### Added
- **Operation log for field-level change tracking (ADR-007 Part 1).**  
  - New `RuntimeFieldOperation` dataclass — a stable, Runtime-native shape for field-granular diffs.  
  - `CoreInterface.field_diff()` — optional Core capability for field-level structural diffing.  
  - `CoreBridge.field_diff()` and `supports_field_diff` — delegation and introspection for the new capability.  
  - `CksCoreAdapter.field_diff()` — computes field-granular diffs between two KnowledgeStructures, reporting `set_field` for modified scalar keys and `add_object`/`remove_object`/`add_relation`/`remove_relation` for identity changes.  
  - `RuntimeStorage.record_operations()` and `supports_operation_log` — optional storage capability for persisting operation logs.  
  - `SQLiteStorage` now maintains a `cks_operation_log` table, implements `record_operations` and `list_operations` (wrapped in `_retry_on_locked`).  
  - `ExecutionPipeline._persist` now calls `_record_operations` after `save_version`/`save_session`, logging field-level changes if both Core and storage support it.  

- Tests: 9 new tests for `CksCoreAdapter.field_diff()`, operation-log tests for `SQLiteStorage`, and coverage for the base `RuntimeStorage` defaults.

### Changed
- Updated `ADR-007` design document to reflect the implemented `field_diff` capability and the decision to use `session_id` as the natural `node_id` for future vector clocks.

---

## [1.15.1] - 2026-07-25

### Added
- **Retry-on-locked helper** (`_retry_on_locked`) for all SQLite write operations, absorbing transient `database is locked` errors under concurrency.
- 12 new tests covering retry behaviour, transient lock survival for every write path, and CAS rejection non-retry.
- **ADR-007** design document proposing an operation log and version vectors for field-level merge conflict resolution and fast-forward detection.

### Changed
- All `SQLiteStorage` methods that perform writes (`save_session`, `save_version`, `enqueue_task`, `complete_outbox_task`, `fail_outbox_task`, `save_object_embeddings`, `delete_object_embeddings`, `clear`) now go through `_retry_on_locked`.

---

## [1.15.0] - 2026-07-25

### Added
- Optimistic concurrency control (OCC) for session saves via `expected_version_id` CAS in `SQLiteStorage`.
- `ConcurrentModificationError` exception for CAS failures.
- `PRAGMA busy_timeout` and retry-on-locked helper for concurrent writes.
- `enqueue_task` base-class no-op default, preventing `AttributeError` on backends without outbox.
- Regression tests for CAS accept/reject and duplicate version-id rejection.

### Changed
- `RuntimeStorage.save_session` now accepts optional `expected_version_id` for OCC.
- `save_version` now uses strict `INSERT` instead of `OR REPLACE`.
- `ExecutionPipeline.commit()` passes the session's pre-transaction version as the CAS anchor.

---

## [1.14.0] - 2026-07-25

### Added
- Abstract outbox lifecycle methods (`dequeue_next_outbox_task`, `complete_outbox_task`, `fail_outbox_task`) in `RuntimeStorage`, with full implementation in `SQLiteStorage`.
- `OutboxEmbeddingWorker` now uses storage abstraction instead of direct `_conn` access, and skips starting when the backend doesn't support outbox.
- `supports_outbox` property on storage backends.
- Worker now computes `previous_version_id` from session history, enabling diff-based incremental embeddings and cleanup of deleted objects.
- `delete_object_embeddings` method for removing embeddings of deleted objects.

### Fixed
- Bug #5: `OutboxEmbeddingWorker` no longer crashes in an infinite loop with `InMemoryStorage`.
- Bug #3: `previous_version_id` is now correctly resolved, making incremental embedding path functional.
- Bug #4: Embeddings for deleted objects are now cleaned up.
- Retry logic: tasks now keep status `PENDING` after failure so they are actually retried.

---

## [1.13.0] - 2026-07-25

### Added
- `CoreBridge.merge()`, `MergeOperation`, and `CksCoreAdapter.merge()` now accept an optional `resolutions` keyword argument for partial three-way merges, delegating directly to `cks-core>=1.10.6`.
- `CoreInterface.merge()` signature updated to include `resolutions` parameter, enabling Core implementations to support conflict resolution.

### Fixed
- Test fakes updated to accept the new `resolutions` parameter, ensuring existing tests pass with the new interface.

---

## [1.11.0] - 2026-07-24

### Added
- `Runtime.create_branch` now automatically records `parent_version_id` when the parent session has a committed version and no active transaction. This makes `merge_branch` work out-of-the-box without needing to pass `version_id` explicitly.
- New regression tests for automatic `parent_version_id` and for failed operation result recording.

### Fixed
- `ExecutionPipeline._execute_operations` now records the operation result **before** failure handling, ensuring that diagnostics from failed operations are accessible to callers who catch the `RuntimeError`.

---

## [1.10.3] - 2026-07-24

### Fixed
- `OutboxEmbeddingWorker` now correctly calls `_execute_task` instead of the removed `_execute_projection`, fixing a crash that prevented any embeddings from being generated after the Task Bus refactoring.

---

## [1.10.2] - 2026-07-24

### Changed
- Embeddings are now normalized to unit length at storage time and query time, enabling fast cosine similarity search via dot product in `search_embeddings`.
- `EmbeddingClient.embed_batch` now accepts an optional `normalize` parameter.
- `OutboxEmbeddingWorker` now stores normalized embeddings.

---

## [1.10.0] - 2026-07-24

### Changed
- **Generalised Task Bus:** Replaced the `cks_projection_outbox` table with a more generic `cks_outbox_tasks` table that supports multiple task types (`projection`, `merge_conflict`, etc.) via a `task_type` column and JSON `payload`.
- `enqueue_outbox_task` now delegates to the new `enqueue_task` method.
- `OutboxEmbeddingWorker` updated to read from the new table structure.

### Migration note
- Existing `cks_projection_outbox` data will NOT be migrated. For fresh installs, the new table is created automatically.

---

## [1.9.6] - 2026-07-24

### Added
- `QuerySubgraphOperation` now accepts an optional `compact_mode` parameter. This allows callers (like `cks-mcp`) to request a compact graph representation without changing the operation's core logic.

---

## [1.9.5] - 2026-07-24

### Fixed
- `SQLiteStorage.load_session` now restores the full `version_history` from the versions table. After a server restart, `get_version_state` works correctly for all previously committed versions.
- `SQLiteStorage.list_sessions` now delegates to `load_session`, ensuring consistent behavior and full history restoration for all sessions.

---

## [1.9.4] - 2026-07-24

### Fixed
- **Critical: `OutboxEmbeddingWorker` now correctly handles delta versions** by reconstructing the full Knowledge Structure via `session.get_version_state()`. Previously, the worker read `knowledge_structure` directly from `RuntimeVersion`, which is `None` for 90% of versions, causing continuous task failures. Embeddings are now generated for all new/modified objects, regardless of snapshot interval.

---

## [1.9.3] - 2026-07-24

### Fixed
- `OutboxEmbeddingWorker` now excludes `CanonicalRelation` objects from embedding generation, preventing relation objects from appearing as false positives in semantic search results.

---

## [1.9.2] - 2026-07-24

### Fixed
- `HuggingFaceEmbeddingClient` now uses the new HuggingFace API endpoint `router.huggingface.co/hf-inference/models/` instead of the deprecated `api-inference.huggingface.co`.

---

## [1.9.1] - 2026-07-24

### Added
- `HuggingFaceEmbeddingClient` — free, token-based embedding client using Hugging Face Inference API. Requires `HF_TOKEN` environment variable.
- `OnnxEmbeddingClient` — local embedding client using ONNX Runtime (requires model download).
- Both clients are interchangeable via the `EmbeddingClient` abstract interface.

### Changed
- `Runtime` now passes `embedding_client` to `OutboxEmbeddingWorker`, enabling real semantic embeddings for `search_semantic`.

---

## [1.9.0] - 2026-07-23

### Added
- `EmbeddingClient` abstract interface for embedding providers.
- `StubEmbeddingClient` (SHA-256 hash-based) for testing.
- `OpenAIEmbeddingClient` for real semantic embeddings via OpenAI API.
- `Runtime` now accepts optional `embedding_client` parameter, passed through to `OutboxEmbeddingWorker`.
- `search_semantic` now uses the configured embedding client for query vectorization.

### Changed
- `OutboxEmbeddingWorker` generates embeddings via the configured client instead of hardcoded SHA-256 stubs.

---

## [1.8.2] - 2026-07-23

### Fixed
- `VersionCreated` event now carries `session_id`, fixing a bug where outbox tasks and embeddings were not associated with the correct session.
- `EmbeddingProjection` now uses `event.session_id` when writing outbox tasks.
- `search_semantic` in cks-mcp (>=1.6.1) can now find embeddings for the correct session.

---

## [1.8.1] - 2026-07-23

### Added
- `SQLiteStorage.search_embeddings()` — searches the embeddings table for the closest vectors to a query embedding.

---

## [1.8.0] - 2026-07-23

### Added
- `OutboxEmbeddingWorker` — background worker that polls the outbox, computes text representations of new/changed Knowledge Objects, generates embeddings (stub implementation with deterministic hashing), and stores them in SQLite.
- `cks_object_embeddings` table in SQLiteStorage for persisting embeddings.
- Worker starts automatically with `Runtime` and processes outbox tasks every 2 seconds.

---

## [1.7.0] - 2026-07-23

### Added
- **Transactional Outbox pattern** for asynchronous embedding generation. `SQLiteStorage` now maintains a `cks_projection_outbox` table.
- **EmbeddingProjection** — listens for `VersionCreated` events and writes tasks to the outbox, ensuring no embedding task is ever lost even if the server restarts.
- New test verifying outbox task creation on version commit.

---

## [1.6.2] - 2026-07-23

### Added
- `MetricsCollector` — tracks invocation counts and execution times per operation type.
- `Runtime.metrics` property exposes the collector.
- `OperationExecutor` records metrics automatically when a collector is present.


---

## [1.6.1] - 2026-07-23

### Fixed
- **Critical: `Dispatcher.dispatch()` now correctly instantiates operations** using `OperationRegistry.create()` instead of passing a class to the executor. Previously, any transaction using `DispatchRequest` would crash with `missing 1 required positional argument: 'executor'`. This bug was hidden by a test that monkey-patched the dispatcher. The test is now rewritten to exercise the real dispatch path.
- Renamed `tests/unit/dispatcher/dispatcher.py` → `test_dispatcher.py` so pytest discovers it.


---

## [1.6.0] - 2026-07-23

### Added
- **Startup persistence restore:** `Runtime` now automatically loads all previously saved sessions and versions from the attached storage backend. After a server restart, all historical sessions are immediately available via `get_session()`, `list_sessions()`, and MCP Resources without any additional configuration.

### Fixed
- `SQLiteStorage` sessions and versions are no longer invisible after a process restart. The `Runtime` now calls `restore` on the `SessionManager` for each persisted session, fully reconstructing `version_history` in the correct chronological order so that delta-based version reconstruction continues to work.
- `InMemoryStorage` behaviour is unaffected (list_sessions returns empty on fresh start, as expected).

---

## [1.5.1] - 2026-07-22

### Changed
- **SQLiteStorage now uses JSON serialization** (via `cks.serialize`/`cks.parse`) instead of `pickle` for storing knowledge structures and patches. This eliminates `MappingProxyType` serialization issues and makes the storage format fully portable and inspectable.
- `RuntimeVersion.__getstate__`/`__setstate__` methods remain for compatibility, but are no longer required for SQLiteStorage.

### Fixed
- Resolved `cannot pickle 'mappingproxy' object` errors that occurred when persisting sessions or versions to SQLite.
- All storage tests (9 new) pass, and full test suite remains at 207+.

### Deprecated
- The previous pickle-based SQLiteStorage implementation is replaced; existing databases from v1.5.0 are not compatible. If you have data from the earlier version, re-create sessions after upgrading.

---

## [1.5.0] - 2026-07-22

### Added
- `SQLiteStorage` — persistent storage backend using SQLite. Sessions and versions survive server restarts.
- `RuntimeConfig.storage_path` — configure the path to the database file (use `:memory:` for in-memory, the default).
- `Runtime` now creates `SQLiteStorage` when `storage_path` is set, otherwise falls back to `InMemoryStorage`.


---

## [1.4.1] - 2026-07-22

### Changed
- Bumped `cks-core` dependency to `>=1.9.1` (includes query_subgraph ordering and relation-as-seed fixes).

---

## [1.4.0] - 2026-07-22

### Added
- `CoreInterface.query_subgraph()` — optional k-hop subgraph extraction capability for Core plugins, mirroring the optional `merge()`/`hash()` contract.
- `CoreBridge.query_subgraph()` and `supports_query_subgraph` — delegate and introspection property for the new capability.
- `QuerySubgraphOperation` — read-only operation (like `ExplainOperation`) that extracts a local neighborhood from a Knowledge Structure, with support for depth, relation/object type filters, and budget/ranking parameters.
- `CksCoreAdapter.query_subgraph()` — delegates to `cks.query_subgraph()` (requires `cks-core>=1.9.0`).
- Bumped `cks-core` dependency to `>=1.9.0`.


---

## [1.3.2] - 2026-07-22

### Changed
- Bumped `cks-core` dependency to `>=1.8.3` (includes deterministic merge order fix).

---

## [1.3.1] - 2026-07-21

### Fixed
- `Runtime.create_branch` now correctly reconstructs the Knowledge Structure from the specified historical version (via `get_version_state`), instead of always branching from the parent's current state.

---

## [1.3.0] - 2026-07-21

### Added
- `CoreInterface.merge()` and `CoreBridge.merge()` – optional three-way merge capability for Core plugins.
- `MergeOperation` – merges a branch session into the current session via `CoreBridge.merge()`.
- `Runtime.create_branch()` and `SessionManager.create_branch()` – explicit branching with parent version tracking.
- `RuntimeMergeConflict` and `RuntimeMergeConflictError` – Runtime-native conflict representation.
- `RuntimeSession.parent_session_id`, `parent_version_id`, `is_branch` – branch lineage metadata.
- `CksCoreAdapter.merge()` – translates `cks.MergeConflictError` into Runtime-native conflict error.
- 21 new tests covering branching, merging, conflict translation, and bridge contracts (total 197 passed).

---

## [1.2.3] - 2026-07-21

### Changed
- Bumped `cks-core` dependency to `>=1.8.2` for three-way merge support.

---

## [1.2.2] - 2026-07-21

### Changed
- Bumped `cks-core` dependency to `>=1.8.0` to benefit from `_id_hash` caching (~10× faster structure construction) and three-way merge support.

---

## [1.2.1] - 2026-07-21

### Fixed
- `DiffOperation` now correctly supports the `target_structure` parameter again, after it was silently dropped during the delta-version refactor.
- `CksCoreAdapter.hash()` now uses the public `root_hash` property instead of the private `_root_hash`.
- `verify_checkpoint` in `RuntimeSession.get_version_state()` now catches both `NotImplementedError` and `RuntimeError`, matching the contract in `VersionManager.create()`.

### Added
- Tests for `DiffOperation` with `target_structure` (2 new tests, total 153 passed).

---

## [1.2.0] - 2026-07-21

### Added
- **Delta version storage:** non-snapshot versions now store only a `patch` (list of structural operators) instead of a full `knowledge_structure`, dramatically reducing memory usage for long version histories.
- `RuntimeVersion.is_snapshot` property and `patch` field.
- `RuntimeSession.get_version_state()` reconstructs any version from the nearest snapshot + patches.
- `VersionManager.create()` accepts `previous_state` and decides snapshot/delta at commit time.
- `ExecutionPipeline` passes `initial_state` to `VersionManager` for correct patch computation.
- Tests for delta version storage and reconstruction.

---

## [1.1.0] - 2026-07-21

### Added
- `CoreInterface.hash()` and `CoreBridge.hash()` — optional content hashing capability for Core plugins.
- `RuntimeVersion.state_hash` — optional integrity hash recorded at version creation.
- `RuntimeSession.get_version_state()` — reconstructs any historical version by replaying diffs from the nearest snapshot, with integrity verification via `state_hash`.
- `VersionManager.create()` now accepts an optional `core_bridge` to populate `state_hash`.
- New test suite for version reconstruction and tamper detection (12 tests).

---

## [1.0.2] - 2026-07-20

### Fixed
- `ExecutionPipeline.commit()` now correctly processes `DispatchRequest`-only transactions (those without legacy `operations`). Previously, transactions built solely with `add_request()` were silently ignored.

---

## [1.0.1] - 2026-07-20

### Fixed
- `CoreBridge.validate` now passes `extra_constraints` even when empty (`is not None` check).
- `TransactionManager._finish` now removes completed transactions from the registry, preventing memory leaks.
- `TransactionManager.get` raises a descriptive `KeyError` when a transaction is not found.
- `Dispatcher.dispatch` no longer writes to the non-existent `context.diagnostics`.
- `ValidateOperation` now correctly returns `FAILED` status when the structure is invalid, preventing invalid structures from being committed as versions.

### Removed
- Deprecated `cks_runtime/adapters/mcp` and the entire `adapters/` package. The canonical MCP server is now `cks-mcp`.

---

## [1.0.0] - 2026-07-20

### Added
- **DiffOperation** – computes structural delta between current session and a target version or structure, producing a compact list of changes.
- `diff` method on `CoreInterface`, `CoreBridge`, and `CksCoreAdapter` – delegates to `KnowledgeStructure.diff()`.
- `ListVersionsOperation` and `RevertVersionOperation` – time‑travel debugging and safe rollbacks.
- `EventBus` integration – `Runtime.events` publishes `TransactionCommitted`, `VersionCreated`, `ValidationFailed`, etc.
- `extra_constraints` passthrough from `CoreInterface` through `ValidateOperation` to `cks-core`.

### Changed
- `ExecutionPipeline` now writes `EvolveOperation` and `RevertVersionOperation` results back to `session.knowledge_structure`.
- Diagnostics are now consolidated through `ExecutionPipeline._handle_result`, eliminating dual tracking.
- `RuntimeTransaction.results` field stores `ExecutionResult` objects, enabling tools to retrieve operation payloads without redundant calls.

### Fixed
- Removed `MappingProxyType` from `RuntimeSession.metadata` to avoid deep‑copy issues.
- `ValidateOperation` no longer treats invalid structure as an operation failure (diagnostics are returned instead of an exception).
- All 161 tests pass.

### Removed
- Legacy `CoreAdapter` references and dead test code.

---

## [0.7.0] - 2026-07-19

### Added
- `ListVersionsOperation` – returns a lightweight history of all versions in the current session, making the audit trail accessible to tools and LLMs.
- `RevertVersionOperation` – restores the Knowledge Structure to any previous version, enabling time‑travel debugging and safe rollbacks.
- `ExecutionPipeline._apply_state_mutation` now handles `RevertVersionOperation` payloads.

---

## [0.6.2] - 2026-07-19

### Added
- `RuntimeTransaction.results` field and `add_result()` method, storing `ExecutionResult` objects produced by each operation in the transaction.
- `ExecutionPipeline` now populates transaction results during execution, enabling downstream tools to retrieve operation payloads directly from the transaction.

### Changed
- `serialize_knowledge` and `explain_knowledge` tools in `cks-mcp` now read operation results from the transaction instead of calling `CoreBridge` a second time. This eliminates redundant computation and improves architectural separation.

---

## [0.6.1] - 2026-07-19

### Changed
- **Diagnostics consolidation.** `OperationExecutor` no longer writes diagnostics directly into the session. The `ExecutionPipeline` now centralises all diagnostic collection via `_handle_result()`, which updates both the global `DiagnosticAggregator` and the session's own diagnostic list. This eliminates the risk of desynchronisation between the two parallel tracking mechanisms.

---

## [0.6.0] - 2026-07-19

### Added
- Event publishing in ExecutionPipeline: `TransactionCommitted`, `TransactionRolledBack`, `TransactionAborted`, `VersionCreated`, `ValidationFailed`.
- Runtime now exposes `events` property for subscribing to internal events.

---

## [0.5.0] - 2026-07-18

### Added
- `extra_constraints` parameter forwarded through `CoreInterface`, `CoreBridge`, `ValidateOperation`, and `CksCoreAdapter` to `cks-core`'s validation API.
- End-to-end regression test confirming `extra_constraints` reaches the Core through the full Runtime pipeline.

---

## [0.4.6] - 2026-07-18

### Fixed
- `RuntimeVersion.metadata` is now properly immutable (wrapped in `MappingProxyType`). Added explicit `__copy__`/`__deepcopy__` to preserve storage isolation for arbitrary `knowledge_structure` types.

---

## [0.4.5] - 2026-07-18

### Fixed
- **ValidateOperation** no longer treats an invalid structure as an operation failure. The validation result is now correctly recorded as diagnostics, and the transaction commits successfully (bug #1).
- **CksCoreAdapter** now translates cks-core Diagnostic objects into cks-runtime-native Diagnostic instances, which use plain dicts for metadata instead of MappingProxyType. This prevents `TypeError: cannot pickle 'mappingproxy' object` when persisting sessions with diagnostics (bug #2).
- Updated integration tests to reflect the corrected contract (invalid structure → committed version with diagnostics, not a raised exception).

---

## [0.4.4] - 2026-07-18

### Fixed
- `ExecutionPipeline` now writes the result of `EvolveOperation` back to the session's `knowledge_structure` before creating a version. This fixes a bug where committed versions silently captured the pre‑evolution structure instead of the evolved result.
- Added regression tests for evolve persistence.

---

## [0.4.3] - 2026-07-18

### Fixed
- Added `__copy__`/`__deepcopy__` to `RuntimeValidationResult` to prevent `cannot pickle 'mappingproxy' object` when sessions containing validation results are persisted.

---

## [0.4.1] - 2026-07-17

### Fixed
- `RuntimeValidationResult.metadata` is no longer wrapped in `MappingProxyType` (resolved `cannot pickle 'mappingproxy' object` error during deep copy).
- All `test_validation_result.py` tests have been updated to match the new behaviour.
- Integration with `cks-mcp` now works without serialization errors.

---

## [0.4.0] - 2026-07-17

### Added
- **Execution Engine** – `ValidateOperation`, `EvolveOperation`, `SerializeOperation`, `ExplainOperation` with implemented `execute` that delegates calls through `CoreBridge`.
- **Dispatcher** and **OperationRegistry** – operation routing by `operation_id`, support for `DispatchRequest`.
- `Runtime` now exposes public properties `sessions`, `transactions`, `versions`, `executor`, `dispatcher`, `operation_registry`.
- `RuntimeTransaction` supports a `requests` list (`DispatchRequest`) and an `add_request` method.
- `ExecutionPipeline` separates ready-made operation objects from dispatcher requests, using `_execute_operations` / `_handle_result`.
- New integration tests for execution flow with a fake Core.

### Changed
- All operations now contain a class-level `operation_id` attribute compatible with `OperationRegistry`.
- `RuntimeSession.metadata` is no longer wrapped in `MappingProxyType` (resolved deep‑copy issue).
- Improved error handling in `ValidateOperation` – a `RuntimeError` is placed in `ExecutionResult` on validation failure.

### Fixed
- The `requests` field in `RuntimeTransaction` is now correctly initialized as a list (`default_factory=list`).
- Eliminated `cannot pickle 'mappingproxy'` and `'Field' object has no attribute 'operation_id'` errors.
- All 147 tests pass consistently.

### Removed
- Outdated references to the old `CoreAdapter` and `MCPTool`.

---

## [0.3.0] - 2026-07-17

### Added
- Public properties `Runtime.sessions`, `Runtime.transactions`, `Runtime.versions`.
- Property `RuntimeSession.has_versions`.
- Module `cks_runtime.storage.exceptions` removed; storage load methods return `None` for missing items.

### Changed
- `CoreBridge.validate` now requires `RuntimeValidationResult` from Core plugin (strict contract).
- `DiagnosticAggregator` accepts any diagnostic objects (Core diagnostics preserved unchanged).
- `ExecutionResult` uses `payload` attribute instead of `data`.
- `RuntimeEvent.timestamp` renamed to `created_at`.
- MCP adapter `__init__` imports `ToolRegistry` directly instead of `MCPToolRegistry`.

### Fixed
- All 141 unit tests pass.
- `InMemoryStorage` deepcopy isolation.
- `ExecutionPipeline` now correctly accesses `runtime.versions` and `runtime.transactions`.
- Various import errors and attribute mismatches resolved.

### Removed
- `MappingProxyType` wrapping of `RuntimeSession.metadata` (caused `deepcopy` issues).
- `MCPTool` class – tests use `FakeTool` dataclass.
- `storage.exceptions` module – removed entirely.

---

## [0.2.0] - 2026-07-16

### Added

- `CksCoreAdapter` — concrete implementation of `CoreInterface` using `cks-core`.
- `cks-runtime-core` package for seamless Runtime/Core integration.
- Integration test suite (89 tests passing).
- PyPI publication (`cks-runtime`).

### Changed

- Project structure: `cks_runtime_core` is now a subpackage of `cks-runtime`.
- `pyproject.toml` updated with `cks-core` dependency.
- `ROADMAP.md` and `README.md` updated to reflect current status.

---

# [0.1.0] - 2026-07-15

## Added

### Runtime Architecture

- Initial Runtime reference architecture
- Specification-first project structure
- Runtime Charter
- Architecture documentation
- Runtime Standards
- Runtime ADRs

### Runtime Components

- Runtime
- Session Manager
- Runtime Session
- Transaction Manager
- Runtime Transaction
- Version Manager
- Runtime Version
- Diagnostic Aggregator
- Diagnostic Model
- Storage Abstraction
- In-Memory Storage

### Core Integration

- Core API abstraction layer
- Explicit dependency boundary to CKS Core

### Testing

- Initial Runtime unit test suite
- Session tests
- Transaction tests
- Versioning tests
- Diagnostics tests
- Storage tests
- Runtime tests

### Project Infrastructure

- Packaging (`pyproject.toml`)
- Documentation
- Security Policy
- Contributing Guide
- Code of Conduct
- Citation metadata
- Roadmap
- README

---

## Notes

This is the first public reference implementation of the CKS Runtime Standard.

CKS Runtime provides the canonical operational environment for Canonical Knowledge Structures while preserving the semantic guarantees established by CKS Core.