# SPEC-003

# Runtime API

**Status:** Draft

**Standard:** CKS Runtime

**Category:** Runtime Interface Specification

---

# 1. Purpose

This specification defines the canonical Runtime API.

The Runtime API specifies the observable operational contract exposed by a Runtime implementation.

It does not define:

- programming language interfaces;
- transport protocols;
- serialization formats;
- implementation techniques.

---

# 2. Scope

This specification defines:

- Runtime operations;
- Session operations;
- operation categories;
- operational guarantees;
- implementation independence.

Concrete language bindings are specified separately.

---

# 3. API Philosophy

The Runtime API exposes operational behaviour.

The Runtime API never exposes semantic behaviour.

Semantic interpretation remains exclusively defined by CKS Core.

The Runtime API therefore represents an operational contract rather than a semantic model.

---

# 4. Architectural Boundary

External systems communicate exclusively through the Runtime API.

The Runtime API forms the architectural boundary between external systems and the Runtime.

```text
Applications
        │
Adapters
        │
Runtime API
        │
Runtime
        │
CKS Core
```

No adapter shall bypass the Runtime API.

---

# 4.1 API Ownership

The Runtime API is owned exclusively by CKS Runtime.

Adapters consume the Runtime API.

CKS Core does not expose Runtime API operations.

The Runtime API shall never become a semantic interface.

Ownership model:

```text
CKS Runtime

    owns Runtime API

        │

Adapters

    consume Runtime API

        │

Applications
```

In the reference implementation, this boundary is realised by the
`Runtime` class in `cks_runtime.runtime`.  The `Runtime` façade
exposes methods that correspond to the canonical capabilities
described in this specification, and it delegates all semantic
calls to the `CksCoreAdapter` from `cks-runtime-core`.

The Runtime API represents operational capabilities only.

---

# 5. Operation Categories

The Runtime API conceptually consists of several categories of operations.

## Session Operations

Operations responsible for managing Runtime Sessions.

Typical capabilities include:

- create Session;
- retrieve Session;
- enumerate Sessions;
- close Session.

---

## Knowledge Operations

Operations responsible for coordinating Runtime lifecycle operations over Canonical Knowledge Structures.

Runtime does not own knowledge semantics.

Knowledge semantics remain exclusively defined by CKS Core.

Typical capabilities include:

- load knowledge;
- retrieve knowledge;
- replace knowledge;
- evolve knowledge.

Knowledge semantics remain delegated to CKS Core.

---

## Validation Operations

Operations responsible for invoking canonical validation.

Validation behaviour is entirely defined by CKS Core.

---

## Transaction Operations

Operations responsible for transactional execution.

Transaction execution is coordinated by Runtime.

Transaction validity and semantic consequences remain defined by CKS Core.

---

## Diagnostics Operations

Operations responsible for retrieving runtime diagnostics.

Diagnostics aggregation is specified separately.

---

## Persistence Operations

Operations responsible for loading and saving runtime state.

Persistence operations operate exclusively on Runtime operational state.

They never persist semantic interpretation independently from CKS Core.

Persistence mechanisms remain implementation-independent.

---

## Version Operations

Operations responsible for runtime history.

Version semantics are specified separately.

---

## Explainability Operations

Operations responsible for coordinating explanation services.

The Runtime API exposes explanations without redefining semantic meaning.

---

# 6. Canonical Capabilities

Every conforming Runtime shall conceptually provide capabilities equivalent to the following.

```text
Session

    create
    retrieve
    close

Knowledge

    load
    retrieve
    evolve request

Validation

    validate

Transactions

    begin
    commit
    rollback

Persistence

    save
    load

Diagnostics

    retrieve

History

    retrieve
```

The reference implementation provides these capabilities as methods
on the `Runtime` class:

| Conceptual Capability | Reference Implementation |
|-----------------------|--------------------------|
| Session create / retrieve / close | `create_session`, `get_session`, `close_session` |
| Knowledge load / retrieve | `create_session` (with `knowledge_structure`) |
| Validation validate | delegated to `CksCoreAdapter.validate` |
| Transactions begin / commit / rollback | `begin_transaction`, `commit_transaction`, `rollback_transaction`, `abort_transaction` |
| Diagnostics retrieve | `runtime.diagnostics` (via `DiagnosticAggregator`) |
| History retrieve | `runtime.versions.latest`, `runtime.versions.list_versions` |

The Runtime API may request knowledge evolution operations.

The resulting semantic evolution is defined exclusively by CKS Core.

The operation names shown above are conceptual.

Runtime implementations may expose equivalent operations using implementation-specific APIs while preserving the observable behaviour defined by this specification.

---

# 6.1 API Result Model

Runtime API results are operational responses.

Conceptually, a Runtime API result may contain:

- operation result;
- Runtime Diagnostics;
- Core Diagnostics;
- Transaction Outcome;
- Runtime metadata.

The Runtime API shall preserve diagnostic ownership.

The Runtime API shall not reinterpret Core Diagnostics.

---

# 7. Session-Centric Design

Every Runtime API operation shall execute within exactly one Session.

The reference implementation enforces this rule: `create_session`
returns a `RuntimeSession`, and `begin_transaction` requires a
valid `RuntimeSession` as its argument.  There are no global
operations that bypass the session context.

No operation shall implicitly operate on global Runtime state.

The Session therefore represents the canonical execution context of the Runtime API.

---

# 8. Determinism

Whenever an operation delegates to deterministic behaviour defined by CKS Core, the observable result shall remain deterministic.

Runtime implementations shall not introduce semantic ambiguity.

Operational behaviour shall preserve the semantic guarantees established by CKS Core.

---

# 9. Operational Failures

Runtime operations may produce operational failures.

Operational failures shall remain distinguishable from semantic diagnostics produced by CKS Core.

Examples of operational failures include:

- unavailable storage;
- transaction conflicts;
- session lifecycle violations;
- implementation resource limitations.

Semantic diagnostics remain exclusively defined by CKS Core.

---

# 10. Transport Independence

This specification intentionally avoids transport-specific behaviour.

The same Runtime API may be exposed through:

- Python;
- CLI;
- MCP;
- HTTP;
- future transports.

Observable behaviour shall remain equivalent across transports.

---

# 11. Extensibility

Future Runtime specifications may introduce additional operational capabilities.

Extensions shall preserve backward compatibility whenever technically possible.

Existing observable behaviour shall not be invalidated.

---

# 12. Conformance

A Runtime implementation conforms to this specification when it:

- exposes the canonical Runtime operational capabilities;
- executes operations within Sessions;
- preserves diagnostic ownership;
- preserves Core semantic authority;
- remains transport-independent.

Conformance to this specification does not imply conformance to the complete Runtime Standard.

---

# 13. Summary

The Runtime API defines the canonical operational contract of the Runtime Standard.

It provides implementation-independent capabilities through which external systems interact with Runtime Sessions while preserving the semantic guarantees established by CKS Core.