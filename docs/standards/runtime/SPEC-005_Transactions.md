# SPEC-005

# Transactions

**Status:** Draft

**Standard:** CKS Runtime

**Category:** Runtime Transaction Specification

---

# 1. Purpose

This specification defines the canonical Runtime Transaction model.

A Runtime Transaction is the canonical mechanism through which Runtime Sessions evolve operational state.

Transactions define the operational execution boundary of Runtime while preserving the canonical semantic guarantees established by CKS Core.

---

# 2. Scope

This specification defines:

* the Transaction concept;
* Transaction ownership;
* Transaction lifecycle;
* Transaction state;
* validation integration;
* diagnostic integration;
* transaction outcomes;
* Runtime responsibilities.

Implementation techniques are intentionally omitted.

---

# 3. Transaction Definition

A Runtime Transaction is an isolated operational unit that coordinates one or more Runtime operations within a Runtime Session.

Transactions define operational execution boundaries.

Transactions control operational state transitions.

Transactions never redefine knowledge semantics.

In the reference implementation, the `RuntimeTransaction` dataclass
holds the transaction state, associated session, operations, and
diagnostics.  The `TransactionManager` class owns the registry of
active transactions and provides the sole interface for beginning,
committing, rolling back, and aborting them.

Semantic interpretation remains exclusively defined by CKS Core.

---

# 4. Transaction Ownership

Every Transaction belongs to exactly one Runtime Session.

A Transaction shall never simultaneously belong to multiple Sessions.

A Runtime coordinates Transactions through its TransactionManager.
Each Transaction belongs to exactly one Runtime Session.

Transaction ownership is operational and shall not be interpreted as semantic ownership.

---

# 5. Transaction State

Conceptually, every Transaction consists of:

* input Session state;
* requested operations;
* validation context;
* diagnostics;
* execution metadata;
* outcome.

Additional implementation-specific state may exist provided that canonical behavior remains unchanged.

---

# 6. Transaction Lifecycle

The canonical Transaction lifecycle is:

```text
Created
    ↓
Executing
    ↓
Validation
    ↓
Completed
````

The reference implementation tracks this lifecycle through the
`TransactionStatus` enum, which defines the states `CREATED`,
`EXECUTING`, `VALIDATING`, `COMMITTED`, `ROLLED_BACK`, and
`ABORTED`.  The `TransactionManager` is responsible for
transitioning transactions through these states.

A Runtime implementation may internally refine this lifecycle provided that the observable behavior defined by this specification is preserved.

The completed state is represented by one of the canonical Transaction outcomes defined by this specification.

---

# 7. Transaction Processing

A Transaction executes within an isolated Runtime Session.

Conceptually, processing follows this sequence:

```text
Input Session State
        │
        ▼
Requested Operations
        │
        ▼
CKS Core Validation
        │
        ▼
Diagnostic Collection
        │
        ▼
Transaction Outcome
        │
        ▼
Session State Transition
```

Runtime coordinates operational execution.

CKS Core determines semantic validity.

Transaction processing shall preserve the separation of operational and semantic responsibilities defined by the Runtime Charter.

---

# 8. Validation

Runtime delegates semantic validation to CKS Core.

Runtime shall never replace or reinterpret canonical validation behavior.

Validation results influence Transaction outcomes but do not alter the semantic meaning of diagnostics.

---

# 9. Diagnostics

Transactions may produce two categories of diagnostics:

* Core Diagnostics;
* Runtime Diagnostics.

Core Diagnostics originate from CKS Core.

Runtime Diagnostics describe operational behavior.

Diagnostic ownership remains defined by SPEC-004.

Transaction processing may collect and expose diagnostics but shall never modify diagnostic meaning or ownership.

---

# 10. Transaction Outcomes

Every Transaction terminates with exactly one outcome.

Canonical outcomes are:

* Committed
* Rolled Back
* Aborted

In the reference implementation, these outcomes correspond to the
`commit`, `rollback`, and `abort` methods of `TransactionManager`.
The `ExecutionPipeline.commit` method invokes Core validation via
`CksCoreAdapter` before calling `TransactionManager.commit`,
ensuring that only semantically valid transactions are committed.

The outcome of a Transaction describes only the operational result of Runtime processing.

Transaction outcomes shall never be interpreted as semantic properties of the underlying Canonical Knowledge Structure.

---

## Committed

Committed indicates that Runtime successfully completed the Transaction and accepted the operational state transition.

Committed Transactions update the operational state of their owning Session.

---

## Rolled Back

Rolled Back indicates that Runtime discarded all pending operational changes.

Rolled Back Transactions shall not modify the previously committed Session state.

---

## Aborted

Aborted indicates that Runtime could not complete Transaction processing.

Aborted Transactions shall preserve the previously committed Session state.

---

# 11. Relationship to Sessions

Transactions execute within Runtime Sessions.

Transactions never exist independently of a Session.

Every observable Runtime state transition shall occur through a Transaction.

Intermediate Transaction states shall not become externally observable Session states.

---

# 12. Relationship to CKS Core

CKS Core is responsible for:

* semantic validation;
* structural validation;
* canonical diagnostics.

Runtime Transactions consume these services.

Runtime Transactions shall never redefine canonical semantics.

---

# 13. Relationship to Storage

Transactions do not define persistence.

Transaction completion and persistence are distinct operational concerns.

Persistence becomes eligible only after successful Transaction completion.

Storage mechanisms are specified separately.

The Storage layer shall never persist partially completed Transactions.

---

# 14. Relationship to Version History

Transactions do not define Version History.

Transaction completion and Version creation are distinct Runtime operations.

A successful committed Transaction may produce a new Runtime Version according to the Version History model.

Version semantics are defined separately.

---

# 15. Transaction Atomicity

Transactions provide the canonical atomicity boundary of Runtime execution.

For every Transaction:

* exactly one outcome is observable;
* intermediate Session states are not externally observable;
* partial operational changes shall not become committed Runtime state.

Atomicity applies to Runtime operational state.

Atomicity does not redefine semantic behavior owned by CKS Core.

---

# 16. Design Principles

The Runtime Transaction model follows these principles.

---

## Operational Isolation

Transactions execute within isolated Session contexts.

---

## Semantic Preservation

Transactions preserve the semantic guarantees established by CKS Core.

---

## Deterministic Coordination

When Runtime operations and CKS Core behavior are deterministic, Transaction outcomes shall remain deterministic.

---

## Single Outcome

Every Transaction terminates with exactly one canonical outcome.

---

## Operational Atomicity

A Transaction shall expose exactly one observable outcome.

Intermediate execution states shall not be externally observable.

---

# 17. Conformance

A Runtime implementation conforms to this specification when it:

* executes operational changes through Transactions;
* associates every Transaction with exactly one Session;
* delegates semantic validation to CKS Core;
* preserves canonical diagnostics;
* preserves diagnostic ownership defined by SPEC-004;
* terminates every Transaction with exactly one canonical outcome;
* preserves the operational isolation and atomicity of Transaction execution.

---

# 18. Summary

Runtime Transactions provide the canonical operational mechanism through which Runtime Sessions evolve.

Transactions define the operational execution boundary through which Runtime state transitions occur.

They coordinate Runtime behavior while preserving the canonical semantic guarantees, validation model and diagnostic semantics established by CKS Core.

Subsequent Runtime specifications build upon the Transaction model introduced by this document.

