# SPEC-006

# Storage

**Status:** Draft

**Standard:** CKS Runtime

**Category:** Runtime Storage Specification

---

# 1. Purpose

This specification defines the canonical Runtime Storage model.

Runtime Storage preserves Runtime operational state beyond the lifetime of an executing Runtime instance.

Storage enables Runtime Sessions to be restored while preserving the canonical semantic guarantees established by CKS Core.

Storage provides persistence capabilities.

Storage does not provide semantic ownership.

---

# 2. Scope

This specification defines:

* the Storage model;
* persistent Runtime state;
* Session persistence;
* Transaction integration;
* Version persistence;
* Runtime responsibilities.

This specification intentionally omits implementation-specific storage technologies.

---

# 3. Storage Philosophy

Storage preserves Runtime operational state.

Storage does not define knowledge semantics.

Storage does not validate knowledge.

Storage does not interpret Canonical Knowledge Structures.

Storage defines persistence behavior, not knowledge behavior.

---

# 4. Storage Model

Runtime Storage is responsible for preserving persistent Runtime state.

Conceptually:

```text
Runtime

      │

Committed Transaction

      │

Runtime State

      │

Storage

      │

Future Runtime
````

In the reference implementation, the `RuntimeStorage` abstract class
defines the canonical interface that every storage backend must
satisfy.  The bundled `InMemoryStorage` provides a deterministic,
in‑memory reference implementation suitable for testing and
single‑process usage.

Storage operates exclusively on Runtime operational state.

Storage shall never become a source of semantic interpretation.

---

# 5. Persistent Runtime State

Persistent Runtime State may include:

* Runtime Sessions;
* committed Transaction results;
* Runtime metadata;
* Runtime Diagnostics;
* Runtime configuration;
* Runtime Version information.

Persistent Runtime State may reference Canonical Knowledge Structures.

However, persistent state shall never redefine canonical knowledge semantics.

---

# 6. Session Persistence

Runtime Sessions may be persisted.

Persisted Sessions may later be restored.

Restoration recreates equivalent Runtime operational state.

Restoration shall not alter:

* canonical knowledge semantics;
* existing Version History;
* previously persisted state.

A restored Session represents a Runtime continuation context.

Session identity remains an operational Runtime concern.

---

# 7. Transaction Integration

Storage receives Runtime state only after successful Transaction completion.

Conceptually:

```text
Session
      │
Transaction
      │
Validation
      │
Commit
      │
Storage
```

Storage observes completed Transaction outcomes only.

Storage shall not participate in Transaction outcome determination.

Intermediate Transaction states shall never become persistent.

Storage shall never persist partially completed Transactions.

---

# 8. Validation Responsibilities

Storage performs no semantic validation.

Storage performs no structural validation.

All canonical validation remains the responsibility of CKS Core.

Runtime coordinates validation before persistence.

Storage assumes that persisted Runtime state has already passed required Runtime Transaction processing.

---

# 9. Diagnostics

Storage may produce Runtime Diagnostics describing persistence behavior.

Examples include:

* persistence failures;
* unavailable storage;
* corrupted storage;
* recovery events.

Storage Diagnostics are Runtime Diagnostics as defined by SPEC-004.

Storage shall never generate, reinterpret or modify canonical diagnostics defined by CKS Core.

---

# 10. Storage Independence

This specification does not prescribe any implementation.

Examples of possible implementations include:

* embedded databases;
* relational databases;
* object storage;
* distributed storage;
* file-based persistence;
* cloud services.

The reference implementation ships with `InMemoryStorage`, which is
deterministic and suitable for testing.  Additional backends (SQLite,
PostgreSQL) are planned for future releases, all implementing the
same `RuntimeStorage` interface.

The choice of storage technology is an implementation concern.

Observable Runtime behavior is the subject of standardization.

Conformance depends on observable Runtime behavior rather than implementation technology.

---

# 11. Relationship to CKS Core

CKS Core remains responsible for:

* canonical knowledge structures;
* semantic validation;
* structural validation;
* canonical diagnostics.

Storage preserves Runtime operational state without redefining canonical semantic state.

---

# 12. Relationship to Runtime Sessions

Storage persists Runtime Sessions.

Storage restores Runtime Sessions.

Storage does not manage Session lifecycle.

Session lifecycle is defined separately by the Runtime Session model.

The reference implementation provides `save_session` and
`load_session` methods on the `RuntimeStorage` interface.
`InMemoryStorage` implements these by deep‑copying the session
state, guaranteeing isolation between persisted and live objects.

---

# 13. Relationship to Transactions

Storage observes Transaction outcomes.

Only successfully committed Transactions become eligible for persistence.

Rolled Back and Aborted Transactions shall not modify persistent Runtime state.

Persistence is a consequence of successful Transaction completion rather than a component of Transaction execution.

---

# 14. Relationship to Version History

Storage provides persistence for Runtime Version History.

Version creation occurs independently according to the Version History model.

Storage preserves Versions but does not define Version semantics.

Storage shall preserve:

* Version identity;
* Version ordering;
* Version immutability.

---

# 15. Storage Lifecycle

Conceptually, Runtime Storage follows this lifecycle:

```text
Initialize
      │
Persist
      │
Restore
      │
Update
      │
Retire
```

This lifecycle describes observable Runtime behavior.

Internal implementation states remain implementation-defined.

Individual implementations may introduce additional internal states provided canonical behavior is preserved.

---

# 16. Design Principles

The Runtime Storage model follows these principles.

## Operational Persistence

Storage preserves Runtime operational state.

---

## Semantic Preservation

Storage never modifies canonical knowledge semantics.

---

## Validation Separation

Validation precedes persistence.

Storage never replaces validation.

---

## Technology Independence

Storage behavior is standardized.

Storage implementation remains unrestricted.

---

## Operational Continuity

Persistent state shall allow Runtime execution to resume without altering canonical semantic behavior.

---

# 17. Conformance

A Runtime implementation conforms to this specification when it:

* preserves Runtime state independently of Runtime process lifetime;
* persists only successfully committed Transaction results;
* separates persistence from semantic validation;
* preserves Version immutability and ordering;
* preserves canonical Runtime behavior independently of storage technology;
* preserves the separation between operational persistence and canonical semantics.

---

# 18. Summary

Runtime Storage defines the canonical persistence model of the Runtime Standard.

Storage enables Session restoration, Version preservation and long-term Runtime continuity while preserving the semantic guarantees defined by CKS Core.

Storage preserves Runtime operational state independently of implementation technology.

Storage provides persistence, not interpretation.

