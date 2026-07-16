# ADR-002

# Session Ownership

**Status:** Accepted

**Date:** 2026-07-15

**Category:** Architecture Decision Record

---

# Context

CKS Runtime manages the operational lifecycle of Canonical Knowledge
Structures.

Runtime operations include:

- loading knowledge;
- validation coordination;
- transactions;
- diagnostics;
- persistence;
- version history.

These operations require operational state that does not belong to CKS Core.

An architectural decision was required regarding ownership of this state.

---

# Problem

Operational state must have a clearly defined owner.

Possible ownership models include:

- Runtime-owned global state;
- transaction-owned state;
- adapter-owned state;
- dedicated Session abstraction.

Without explicit ownership, runtime state becomes difficult to isolate,
persist and reason about.

---

# Decision

Every knowledge lifecycle operation shall execute within exactly one
Runtime Session.

The reference implementation encodes this decision in the
RuntimeSession dataclass (cks_runtime.session.session), which
holds all operational state for a single execution context.

The Session is the canonical owner of Runtime operational state.

Knowledge lifecycle operations shall never execute outside a Session.

---

# Responsibilities

A Runtime Session owns:

- Runtime state associated with a Canonical Knowledge Structure;
- runtime metadata;
- transaction context;
- diagnostics;
- version history;
- storage context;
- explainability context.

Session ownership applies only to operational state.

Session ownership does not grant ownership of Core semantics.

Semantic meaning remains exclusively owned by CKS Core.

---

# Session Identity

Every Session possesses a Runtime-defined identity.

Session identity:

- is operational;
- is implementation-independent;
- is not a canonical object identity;
- shall never replace identities defined by CKS Core.

In the reference implementation, the session identity is a UUID
generated automatically by RuntimeSession.__init__.

---

# Dependency Model

```text
CKS Runtime

        │
        │ manages lifecycle of
        ▼

Runtime Session

        │
        │ references
        ▼

Canonical Knowledge Structure

        │
        │ defined by
        ▼

CKS Core
```

The reference implementation stores the reference to the
Canonical Knowledge Structure in
RuntimeSession.knowledge_structure.

Runtime manages lifecycle.

CKS Core defines semantics.

The reference implementation delegates session ownership to the
SessionManager class, which maintains a registry of active
sessions and enforces the rule that each session belongs to
exactly one Runtime instance.

---

# Consequences

Positive consequences include:

* explicit ownership of Runtime state;
* isolation between independent executions;
* deterministic lifecycle management;
* simplified persistence;
* simplified version history;
* simplified diagnostics aggregation.

Negative consequences include:

* additional abstraction;
* explicit Session lifecycle management;
* Session coordination requirements.

These trade-offs are considered acceptable.

---

# Alternatives Considered

## Global Runtime State

Store operational state directly within Runtime.

Rejected because multiple execution contexts become difficult to isolate.

---

## Adapter-Owned State

Allow adapters to maintain Runtime state.

Rejected because Runtime behaviour becomes transport-dependent.

---

## Transaction-Owned State

Allow Transactions to own Runtime state.

Rejected because operational state exists before and after individual
Transactions.

---

# Rationale

The Session represents the natural operational boundary of Runtime.

It groups operational concerns that belong together while remaining
independent from canonical knowledge semantics.

This separation enables multiple isolated execution contexts while
preserving the semantic authority of CKS Core.

---

# Relationship to Specifications

This decision is reflected by:

* SPEC-002 Session Model;
* SPEC-003 Runtime API;
* SPEC-005 Transactions;
* SPEC-006 Storage;
* SPEC-007 Version History.

Future Runtime specifications shall preserve Session ownership.

---

# Status

Accepted.

Every conformant Runtime implementation shall preserve Session ownership
as the canonical operational boundary for knowledge lifecycle operations.
