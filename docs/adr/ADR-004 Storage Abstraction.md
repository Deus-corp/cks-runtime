# ADR-004

# Storage Abstraction

**Status:** Accepted

**Date:** 2026-07-15

**Category:** Architecture Decision Record

---

# Context

CKS Runtime requires persistence capabilities to preserve operational
state beyond the lifetime of an executing Runtime instance.

Runtime must support:

- Session restoration;
- Version History preservation;
- Runtime continuity;
- recovery after process termination.

Persistence technologies vary significantly between implementations.

Possible storage technologies include:

- in-memory storage;
- filesystem storage;
- embedded databases;
- relational databases;
- distributed storage;
- cloud storage.

An architectural decision is required regarding the ownership and
abstraction boundary of persistence.

---

# Problem

If Runtime directly depends on a specific storage implementation:

- Runtime becomes coupled to infrastructure;
- portability is reduced;
- testing becomes more difficult;
- alternative storage implementations become expensive.

If Storage owns semantic knowledge:

- Core responsibilities are duplicated;
- semantic boundaries are violated;
- Runtime becomes dependent on storage representation.

A clear separation is required.

---

# Decision

CKS Runtime shall use an implementation-independent Storage abstraction.

The reference implementation provides this abstraction through the
RuntimeStorage abstract class in cks_runtime.storage.storage,
which defines the canonical interface that every storage backend
must satisfy.

Storage is responsible only for persistence of Runtime operational state.

Storage shall never define, validate or interpret knowledge semantics.

Storage shall preserve Runtime state without changing its observable meaning.

---

# Ownership Model

Two independent ownership relationships exist.

## Semantic Authority

```text
CKS Core

    defines

Canonical Knowledge Semantics
````

---

## Operational Persistence

```text
CKS Runtime

    owns

Runtime Operational State

        |

        v

Storage

    persists

Runtime State
```

Storage is subordinate to Runtime.

Storage does not own:

* Sessions;
* Transactions;
* Version semantics;
* Knowledge semantics.

---

# Storage Responsibilities

Storage may provide:

* persistence of Sessions;
* persistence of committed Runtime state;
* persistence of Versions;
* persistence of Runtime metadata;
* restoration capability.

The reference implementation ships with InMemoryStorage, a
deterministic in‑memory backend that implements the
RuntimeStorage interface and is suitable for testing and
single‑process usage.

Storage shall not provide:

* semantic validation;
* knowledge interpretation;
* transaction decisions;
* lifecycle ownership;
* recovery policy.

---

# Transaction Boundary

Persistence occurs only after successful Transaction completion.

The canonical flow is:

```text
Session

    │

Transaction

    │

Commit

    │

Version Creation

    │

Storage Persistence
```

In the reference implementation, this flow is enforced by the
ExecutionPipeline.commit method, which calls
self._runtime.storage.save_version and
self._runtime.storage.save_session only after a successful
transaction commit.

Storage shall never:

* commit Transactions;
* decide Transaction outcomes;
* persist partially completed Transactions.

---

# Recovery Boundary

Storage provides persistence and restoration capabilities.

Runtime owns:

* recovery process;
* Session reconstruction;
* lifecycle restoration.

The reference implementation provides load_session and
load_version methods on the RuntimeStorage interface, with
corresponding error types (SessionNotFound, VersionNotFound)
defined in cks_runtime.storage.exceptions.

---

# Consequences

Positive consequences include:

* storage technology independence;
* simpler Runtime testing;
* deterministic persistence boundaries;
* clean separation between execution and infrastructure;
* support for multiple deployment models.

Negative consequences include:

* additional abstraction layer;
* explicit Storage interface design;
* possible limitations imposed by abstraction.

These trade-offs are considered acceptable.

---

# Alternatives Considered

## Direct Database Dependency

Runtime directly manages a database implementation.

Rejected because Runtime becomes coupled to a specific persistence technology.

---

## Storage-Owned Lifecycle

Storage manages Sessions and Transactions.

Rejected because lifecycle ownership belongs to Runtime.

---

## Knowledge-Aware Storage

Storage understands canonical objects, relations and semantics.

Rejected because semantic ownership belongs exclusively to CKS Core.

---

# Rationale

Runtime must remain the authority over operational lifecycle.

Storage is an infrastructure capability, not an architectural owner.

The Storage abstraction allows Runtime to evolve independently from
persistence technologies while preserving deterministic observable
behaviour.

---

# Relationship to Specifications

This decision is reflected by:

* SPEC-006 Storage;
* SPEC-005 Transactions;
* SPEC-007 Version History;
* SPEC-002 Session Model.

Future Runtime specifications shall preserve Storage abstraction.

---

# Status

Accepted.

Every conformant Runtime implementation shall interact with persistence
mechanisms exclusively through the Runtime Storage abstraction.
