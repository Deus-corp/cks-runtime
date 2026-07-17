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