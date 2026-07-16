# ADR-003

# Transaction Model

**Status:** Accepted

**Date:** 2026-07-15

**Category:** Architecture Decision Record

---

# Context

Runtime Sessions evolve over time.

Operations performed during a Session may:

- request evolution of Canonical Knowledge Structures;
- update Runtime operational state;
- generate diagnostics;
- create Version History entries;
- persist committed Runtime state.

These operations must remain deterministic and recoverable.

---

# Problem

Without an explicit execution model, Runtime state could be modified
incrementally by unrelated operations.

Such an approach complicates:

- rollback;
- persistence boundaries;
- diagnostics;
- version history;
- reproducibility.

An explicit coordination mechanism is therefore required.

---

# Decision

Every semantic or lifecycle-relevant modification of Session state shall
occur through a Runtime Transaction.

Transactions are the exclusive mechanism for coordinated Runtime state
transitions.

The reference implementation enforces this rule through the
TransactionManager class, which owns the registry of active
transactions and provides the sole interface for beginning,
committing, rolling back, and aborting them.

Runtime components shall not directly modify Session lifecycle state.

---

# Responsibilities

A Transaction coordinates:

- requested operations;
- interaction with CKS Core;
- validation execution;
- diagnostic collection;
- operational state transition;
- commit decision.

A Transaction does not define knowledge semantics.

CKS Core remains responsible for:

- semantic interpretation;
- validation rules;
- evolution semantics.

---

# Transaction Identity

Every Transaction possesses a Runtime-defined identity.

Transaction identity:

- is operational;
- is implementation-independent;
- is not a canonical identity;
- shall never replace identities defined by CKS Core.

In the reference implementation, the transaction identity is a UUID
generated automatically by RuntimeTransaction.__init__.

---

# Execution Model

Conceptually:

```text
Session

    │

Begin Transaction

    │

Requested Operations

    │

CKS Core Processing

    │

Validation

    │

Commit / Rollback / Abort

    │

Updated Session State
```

The reference implementation realises this execution model in the
ExecutionPipeline.commit method: it first invokes Core validation
via CksCoreAdapter.validate, then either commits the transaction
through TransactionManager.commit or rolls it back, depending on
the validation outcome.

All lifecycle-relevant state transitions occur inside the Transaction
boundary.

---

# Commit Responsibilities

A successful Transaction commit:

* makes the resulting Session state available;
* enables Version creation;
* enables persistence.

Transaction commit does not itself create Versions or persist data.

In the reference implementation, the commit flow is orchestrated by
ExecutionPipeline.commit, which calls CksCoreAdapter.validate
before delegating the final commit to TransactionManager.commit.
This ensures that no transaction is committed without prior
semantic validation by CKS Core.

---

# Ownership Boundaries

The following ownership rules apply:

| Responsibility           | Owner               |
| ------------------------ | ------------------- |
| Transaction coordination | Transaction Manager |
| Semantic evolution       | CKS Core            |
| Version creation         | Version Manager     |
| Persistence              | Storage Manager     |
| Diagnostics aggregation  | Diagnostics Manager |

---

# Consequences

Positive consequences include:

* atomic state transitions;
* deterministic execution;
* simplified rollback;
* clear persistence boundaries;
* clear Version History creation;
* simplified diagnostics aggregation.

Negative consequences include:

* additional execution layer;
* explicit Transaction lifecycle management;
* more defined component boundaries.

These trade-offs are considered acceptable.

---

# Alternatives Considered

## Direct Session Mutation

Allow Runtime components to modify Session state directly.

Rejected because operational changes become difficult to reason about and
recover.

---

## Component-Owned State Changes

Allow individual Runtime components to update state independently.

Rejected because ordering and rollback become implementation-dependent.

---

## Storage-Driven Updates

Treat persistence as the primary state transition mechanism.

Rejected because Runtime state should exist independently of persistence.

---

# Rationale

Transactions provide a deterministic operational boundary.

Every observable Runtime lifecycle transition becomes explicit.

This simplifies:

* debugging;
* replay;
* persistence;
* recovery;
* testing.

---

# Relationship to Specifications

This decision is reflected by:

* SPEC-002 Session Model;
* SPEC-003 Runtime API;
* SPEC-005 Transactions;
* SPEC-006 Storage;
* SPEC-007 Version History.

Future Runtime specifications shall preserve Transaction-centric execution.

---

# Status

Accepted.

Every conformant Runtime implementation shall evolve Session lifecycle
state exclusively through Transactions.
