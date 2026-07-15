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
