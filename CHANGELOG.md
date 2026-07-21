# Changelog

All notable changes to CKS Runtime will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

---

# [Unreleased]

## Planned

- Adapter implementations (MCP, CLI, HTTP)
- Runtime Facade
- Explainability integration
- Runtime Conformance Suite
- Storage backends beyond in-memory
- CKS Core integration package

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