# SPEC-008

# Runtime Conformance

**Status:** Draft

**Standard:** CKS Runtime

**Category:** Runtime Conformance Specification

---

# 1. Purpose

This specification defines the conformance requirements for implementations claiming compatibility with the CKS Runtime Standard.

Conformance guarantees that independent Runtime implementations preserve equivalent observable Runtime behaviour when executing equivalent operational lifecycles over Canonical Knowledge Structures.

This specification defines observable Runtime behaviour only.

It intentionally does not prescribe implementation techniques, internal architecture or infrastructure choices.

---

# 2. Scope

This specification applies to every implementation claiming compatibility with:

- SPEC-001 Runtime Overview
- SPEC-002 Session Model
- SPEC-003 Runtime API
- SPEC-004 Diagnostics
- SPEC-005 Transactions
- SPEC-006 Storage
- SPEC-007 Version History

This specification defines the conditions required for Runtime conformance.

It does not define implementation techniques.

---

# 3. Architectural Foundation

Runtime conformance is evaluated according to the architectural decisions established by:

- ADR-001 Runtime Layering
- ADR-002 Session Ownership
- ADR-003 Transaction Model
- ADR-004 Storage Abstraction
- ADR-005 Version History
- ADR-006 Adapter Architecture

A conformant Runtime implementation shall preserve these architectural boundaries.

---

# 4. Conformance Model

Conformance is binary.

An implementation is either:

- Conformant;
- Non-conformant.

Partial conformance is not defined.

Every mandatory requirement defined by the Runtime Standard shall be satisfied.

Failure to satisfy any mandatory requirement results in non-conformance.

Conformance describes compatibility with the Runtime Standard, not implementation maturity or feature completeness beyond the Standard.

---

# 5. Mandatory Behaviour

A conformant Runtime shall:

- preserve all CKS Core semantic invariants;
- operate exclusively on Canonical Knowledge Structures;
- preserve Session ownership rules;
- execute operational state changes through Transactions;
- preserve Transaction isolation;
- preserve Version immutability;
- maintain ordered Version History;
- aggregate diagnostics without semantic modification;
- expose Runtime capabilities through the Runtime API;
- preserve transport-independent observable behaviour.

The reference implementation satisfies these requirements through the
`Runtime` façade, `SessionManager`, `TransactionManager`,
`VersionManager`, and `ExecutionPipeline` classes in `cks-runtime`.

---

# 6. Semantic Preservation

A conformant Runtime shall never redefine semantic behaviour.

Runtime shall never:

- introduce additional semantic rules;
- replace Core validation;
- reinterpret Core diagnostics;
- modify canonical identities;
- alter canonical serialization meaning;
- redefine knowledge semantics.

All semantic authority remains exclusively within CKS Core.

---

# 7. Transaction Conformance

A conformant Runtime shall:

- execute Session state changes exclusively through Transactions;
- associate every Transaction with exactly one Session;
- produce exactly one Transaction outcome;
- preserve atomic operational behaviour;
- prevent externally observable intermediate states.

In the reference implementation, transaction atomicity and isolation
are enforced by the `ExecutionPipeline.commit` method, which invokes
Core validation via `CksCoreAdapter` before committing the
transaction through `TransactionManager.commit`.

Transaction behaviour shall remain consistent with SPEC-005.

---

# 8. Version History Conformance

A conformant Runtime shall:

- create Versions only after successful Transaction completion;
- preserve Version immutability;
- preserve Version ordering;
- preserve historical Runtime evolution;
- support restoration according to Version History rules.

The reference implementation guarantees version immutability through
the `RuntimeVersion` frozen dataclass, which performs a deep copy of
the `knowledge_structure` and `metadata` fields in `__post_init__`.

Existing Versions shall never be modified or rewritten.

---

# 9. Diagnostics Conformance

A conformant Runtime shall:

- preserve Core Diagnostics unchanged;
- distinguish Core Diagnostics from Runtime Diagnostics;
- aggregate diagnostics without modifying ownership or meaning;
- expose diagnostics through Runtime interfaces.

The reference implementation satisfies these requirements through the
`DiagnosticAggregator` class and the `RuntimeValidationResult`
dataclass, which together preserve Core diagnostic ownership and
separate Core from Runtime diagnostics.

Runtime diagnostics may describe operational behaviour only.

---

# 10. Storage Independence

Conformance shall not depend on storage technology.

The following implementations may all conform:

- in-memory storage;
- file-based storage;
- embedded databases;
- relational databases;
- distributed storage;
- cloud storage.

Storage technology may influence:

- performance;
- scalability;
- deployment characteristics.

Storage technology shall not alter observable Runtime behaviour.

The reference implementation ships with `InMemoryStorage`, a
deterministic in‑memory backend that implements the `RuntimeStorage`
interface.  Additional backends (SQLite, PostgreSQL) are planned for
future releases and will adhere to the same interface.

---

# 11. Transport Independence

Runtime conformance shall not depend on transport protocol.

Equivalent Runtime operations may be exposed through:

- Python APIs;
- CLI interfaces;
- MCP adapters;
- HTTP APIs;
- future transport mechanisms.

Adapters shall expose Runtime behaviour without changing Runtime meaning.

---

# 12. Deterministic Behaviour

Given equivalent:

- Runtime Session state;
- Transaction sequence;
- Runtime configuration;
- Core semantic behaviour;

a conformant Runtime shall produce equivalent observable results including:

- Session state transitions;
- Version History;
- diagnostics;
- Transaction outcomes;
- Runtime API responses.

The reference implementation achieves determinism through immutable
data structures (`frozen` dataclasses, `deepcopy` in
`RuntimeVersion.__post_init__`) and the predictable behaviour of
`InMemoryStorage`.

Internal implementation details may differ.

---

# 13. Core Compatibility

CKS Core defines canonical semantic correctness.

CKS Runtime defines canonical operational correctness.

A conformant Runtime shall preserve the semantics of every compatible CKS Core implementation.

Runtime remains strictly layered above CKS Core.

CKS Core shall never depend on Runtime.

---

# 14. Conformance Test Suite

Future Runtime implementations shall be validated through an official Runtime Conformance Suite.

The suite shall verify:

- Session lifecycle;
- Session ownership;
- Transaction execution;
- Transaction outcomes;
- Version immutability;
- Version History ordering;
- diagnostics aggregation;
- Storage abstraction behaviour;
- Runtime API behaviour;
- transport independence.

Future editions may extend the Conformance Suite while preserving existing observable requirements.

The current test suite (89 tests, located in `tests/unit/`) already
covers sessions, transactions, versioning, diagnostics, storage, and
runtime integration, providing a foundation for the future official
Conformance Suite.

---

# 15. Version Declaration

Conformance is evaluated against a specific Runtime Standard version.

A Runtime implementation shall declare the Runtime Standard version against which conformance is claimed.

Example:

```

CKS Runtime Standard v1.0

```

---

# 16. Non-goals

This specification intentionally does not define:

- implementation language;
- storage engine;
- concurrency implementation;
- deployment architecture;
- transport protocol;
- optimization strategy.

---

# 17. Relationship to CKS Core

CKS Core defines:

- canonical knowledge;
- semantic validity;
- canonical diagnostics;
- semantic evolution rules.

CKS Runtime defines:

- operational lifecycle;
- execution coordination;
- persistence orchestration;
- runtime state management.

A conformant Runtime implementation preserves this separation.

---

# 18. Summary

This specification defines the conditions under which an implementation may claim conformance with the CKS Runtime Standard.

Conformance is determined exclusively by observable Runtime behaviour.

Implementation techniques remain unrestricted provided that:

- Runtime architectural boundaries are preserved;
- operational guarantees are satisfied;
- CKS Core semantic authority remains unchanged.

CKS Runtime conformance therefore represents compatibility with a stable operational contract while preserving a single canonical definition of knowledge established by CKS Core.
