# ADR-005

# Version History

**Status:** Accepted

**Date:** 2026-07-15

**Category:** Architecture Decision Record

---

# Context

CKS Runtime manages evolving Canonical Knowledge Structures through Sessions
and Transactions.

Every successful Transaction may change the operational state of a Session.

Runtime requires a mechanism to:

- preserve previous states;
- reconstruct historical state;
- inspect evolution over time;
- support deterministic restoration;
- provide operational traceability.

A decision is required regarding the ownership, identity and mutation model
of Runtime history.

---

# Problem

Several possible history models exist:

1. Mutable current-state storage only.
2. Mutable history records.
3. Immutable version snapshots.
4. Event-only history.

The Runtime architecture requires a model that preserves deterministic
behaviour, prevents historical ambiguity and maintains a clear separation
between operational history and semantic knowledge.

---

# Decision

CKS Runtime shall represent history as an ordered collection of immutable
Runtime Versions.

Every committed Transaction that produces an observable Session state
transition shall create a new Version.

Existing Versions shall never be modified.

Runtime history is append-only.

---

# Version Definition

A Runtime Version is an immutable snapshot of Runtime Session state.

A Version represents the operational state of a Runtime Session immediately
following a successful Transaction commitment.

A Version may contain:

- the associated Canonical Knowledge Structure;
- Runtime metadata;
- operational state required for restoration;
- diagnostic and lifecycle information.

Versions are Runtime artifacts.

They do not redefine knowledge semantics.

Semantic meaning remains exclusively defined by CKS Core.

---

# Version Ownership

Version ownership belongs exclusively to Runtime.

The ownership hierarchy is:

```text
CKS Core

    defines knowledge semantics

        │

Runtime Session

    owns operational evolution

        │

Transaction

    produces committed state transition

        │

Version

    records resulting Session state
````

Versions are not Core semantic objects.

They shall never replace or modify canonical identities defined by CKS Core.

---

# Version Identity

Each Version possesses a unique Runtime identity.

Version identities are operational identifiers.

They shall never be interpreted as:

* KnowledgeObject identities;
* canonical relation identities;
* Core semantic identifiers.

Version identity ownership belongs exclusively to Runtime.

---

# Version Creation Rules

A Version shall be created after successful Transaction commitment when the
Transaction produces an observable Session state transition.

The canonical flow is:

```text
Session

    │

Transaction

    │

Validation

    │

Commit

    │

Version Creation
```

The following shall never create Versions:

* failed Transactions;
* rolled back Transactions;
* aborted Transactions;
* non-observable internal Runtime operations;
* Runtime initialization.

---

# Immutability

Versions are immutable snapshots.

After creation:

* Version content cannot change;
* Version identity cannot change;
* Version metadata cannot change;
* Version ordering cannot change.

Any new Runtime state shall create a new Version.

---

# History Model

Runtime History follows an append-only model.

Conceptually:

```text
Version 1
     │
Version 2
     │
Version 3
     │
Version N
```

History ordering represents Runtime evolution.

Historical ordering is part of observable Runtime behaviour.

---

# Version Lineage

Each Version belongs to an ordered Runtime lineage.

A Version may reference its immediate parent Version.

Version lineage provides:

* deterministic history traversal;
* restoration reasoning;
* explainability support.

Version lineage is an operational Runtime concept.

It does not define semantic relationships between knowledge entities.

---

# Restoration

Runtime may restore Session state from a previously persisted Version.

Conceptually:

```text
Version

      │

Restore

      │

Runtime Session
```

Restoration creates operational state equivalent to the selected Version.

Restoration shall:

* preserve the original Version;
* create no semantic changes;
* not modify existing history;
* not rewrite Version lineage;
* not resume previous Transactions.

A restored Session continues evolution by creating new Versions.

---

# Relationship to Transactions

Transactions are the only mechanism capable of producing new Versions.

A Version represents the result of exactly one successful committed
Transaction.

Relationship:

```text
Transaction
      │
      ▼
Version
```

A Version without an originating committed Transaction is invalid.

---

# Relationship to Storage

Storage persists Versions.

Storage does not define Version semantics.

Storage shall preserve:

* Version immutability;
* Version ordering;
* Version identity;
* Version lineage.

The Storage implementation remains independent.

---

# Relationship to Runtime Sessions

Runtime Sessions evolve through Transactions.

Successful observable Transactions create Versions.

Version History records Session evolution over time.

---

# Relationship to CKS Core

CKS Core defines:

* canonical knowledge semantics;
* canonical validation;
* semantic correctness.

Runtime Version History records operational evolution only.

Versions shall never redefine:

* object meaning;
* relation meaning;
* validation rules;
* semantic correctness.

---

# Diagnostics

Runtime may produce diagnostics related to Version History.

Examples include:

* restoration failure;
* unavailable historical Version;
* corrupted Version metadata.

Such diagnostics are Runtime Diagnostics.

They shall never redefine canonical semantic diagnostics.

---

# Consequences

## Positive Consequences

This decision provides:

* deterministic history;
* reproducible Runtime state;
* auditability;
* explainability foundation;
* reliable restoration;
* simpler conformance testing.

---

## Negative Consequences

This decision introduces:

* additional storage requirements;
* immutable data management complexity;
* possible growth of historical data.

These trade-offs are acceptable because historical determinism is a core
Runtime requirement.

---

# Alternatives Considered

## Mutable History Records

Rejected.

Mutable history allows historical ambiguity and prevents reliable
reconstruction.

---

## Current State Only

Rejected.

Current state does not provide evolution tracking or deterministic
restoration.

---

## Event-Only Model

Rejected for the initial Runtime architecture.

Event sourcing may be implemented internally, but observable Runtime
behaviour shall remain Version-based.

---

# Rationale

Runtime exists to manage the lifecycle of knowledge.

Lifecycle management requires the ability to observe how Runtime state
evolved.

Immutable Version History provides a stable operational record without
introducing semantic ownership into Runtime.

---

# Relationship to Specifications

This decision is reflected by:

* SPEC-005 Transactions;
* SPEC-006 Storage;
* SPEC-007 Version History;
* SPEC-008 Conformance.

Future Runtime specifications shall preserve immutable append-only
Version History.

---

# Status

Accepted.

Every conformant Runtime implementation shall preserve Runtime history as
an immutable ordered sequence of Versions.
