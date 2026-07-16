# ANALYSIS-001

## Runtime Dependencies on CKS Core

**Status:** Draft

**Category:** Architectural Analysis

**Applies to:** CKS Runtime Standard

---

# 1. Purpose

This document analyses the architectural relationship between **CKS Runtime** and **CKS Core**.

Its purpose is to identify:

- the responsibilities permanently owned by CKS Core;
- the responsibilities delegated to CKS Runtime;
- architectural boundaries between the two standards;
- immutable dependency rules that shall remain valid throughout the evolution of the CKS ecosystem.

This document establishes the architectural foundation for every subsequent Runtime specification.

All Runtime specifications shall remain consistent with the dependency rules and responsibility boundaries defined herein.

---

# 2. Scope

This document defines architectural ownership and dependency rules.

It does not define:

- Runtime APIs;
- storage implementations;
- execution models;
- transport protocols.

Those topics are specified separately within the Runtime Standard.

---

# 3. Referenced Core Specifications

CKS Runtime depends on the canonical semantics defined by the CKS Core Standard, including (but not limited to):

- CKS-001 — Canonical Knowledge Model
- CKS-002 — Canonical Object Model
- CKS-003 — Canonical Relation Model
- CKS-004 — Canonical Serialization
- CKS-005 — Validator Specification
- CKS-006 — Reference Engine
- CKS-007 — Canonical Knowledge Interface
- CKS-008 — Reference Conformance Specification

Future Core specifications automatically extend this dependency unless explicitly stated otherwise.

These specifications collectively define the semantic contract consumed by CKS Runtime.

---

# 4. Fundamental Terminology

CKS Runtime shall adopt the canonical terminology defined by CKS Core.

Runtime shall not redefine existing semantic concepts.

Whenever terminology differs between Runtime documents and Core documents, the Core definition shall prevail.

Examples include:

- KnowledgeStructure
- KnowledgeObject
- CanonicalRelation
- ObjectIdentity
- Validation
- Constraint
- Diagnostic
- Evolution
- Serialization

The Core Standard remains the single authoritative source of terminology.

---

# 5. Architectural Principle

The CKS ecosystem is divided into independent architectural layers.

```text
Applications
        │
        ▼
Adapters
        │
        ▼
CKS Runtime
        │
        ▼
CKS Core
```

Each layer has a single architectural responsibility.

Each layer may depend only on the layer immediately below it.

Dependencies flow downward.

Semantic authority originates exclusively from CKS Core.
Operational behaviour originates from CKS Runtime.

---

# 5.1 Architectural Authority Model

The CKS ecosystem defines three authority domains.

## Semantic Authority

Owned exclusively by CKS Core.

Includes:

- knowledge meaning;
- validation;
- constraints;
- canonical identities.

## Operational Authority

Owned exclusively by CKS Runtime.

Includes:

- lifecycle;
- sessions;
- transactions;
- persistence;
- history.

## Exposure Boundary

Owned by Adapters.

Includes:

- protocol translation;
- external representation;
- transport concerns.

No authority domain may absorb responsibilities belonging to another.

---

# 6. Responsibilities of CKS Core

CKS Core exclusively owns the semantic definition of knowledge.

Core responsibilities include:

- canonical knowledge model;
- canonical object model;
- canonical relation model;
- canonical identities;
- canonical serialization;
- canonical parsing;
- validation framework;
- canonical constraints;
- canonical semantic diagnostics model;
- knowledge evolution;
- semantic invariants;
- conformance requirements.

Core defines what knowledge **is**.

Core does not define how knowledge is executed, stored, managed, or transported.

---

# 7. Responsibilities of CKS Runtime

CKS Runtime exclusively owns the operational management of Canonical Knowledge Structures.

Runtime responsibilities include:

- session management;
- lifecycle management;
- transaction management;
- version history;
- persistence orchestration;
- storage abstraction;
- runtime diagnostics aggregation;
- explainability orchestration;
- operational execution environment;
- runtime state management.

Runtime defines how knowledge is **managed**.

Runtime never changes what knowledge **means**.

---

# 8. Forbidden Responsibilities

CKS Runtime shall never own or redefine:

- canonical semantics;
- canonical identities;
- canonical validation;
- canonical constraints;
- canonical serialization format;
- canonical evolution rules;
- canonical diagnostics model;
- duplicate semantic behaviour already defined by CKS Core;
- become an alternative semantic authority.

These responsibilities permanently belong to CKS Core.

---

# 9. Dependency Rules

The dependency direction is immutable.

The following rules shall always hold:

- CKS Core shall not depend on Runtime.
- Runtime may depend on Core.
- Adapters may depend on Runtime.
- Applications should interact with the ecosystem through Adapters.
- Direct Application dependency on Core or Runtime internals is discouraged.

Reverse dependencies are prohibited.

---

# 9.1 Semantic Integration Boundary

CKS Runtime communicates with CKS Core exclusively through the public semantic API exposed by CKS Core.

Runtime shall never depend upon internal implementation details of CKS Core.

Official Runtime implementations may provide automatic integration with the official CKS Core package.

Such integration is an implementation convenience rather than an architectural dependency.

The reference implementation of this integration is provided by the
cks-runtime-core package, which contains CksCoreAdapter — a
concrete implementation of CoreInterface that delegates every
semantic call to cks-core.

The architectural dependency remains:

```text
Runtime
        │
        ▼
Public CKS Core API
        │
        ▼
CKS Core
```

The Runtime Standard standardizes the operational contract.

The CKS Core Standard standardizes the semantic contract.

The public API constitutes the only supported architectural boundary between the two standards.

---

# 10. Compatibility Model

Conforming Runtime implementations shall operate exclusively on Canonical Knowledge Structures defined by CKS Core.

Runtime shall never require alternative knowledge representations.

A Runtime implementation shall declare the Core Standard versions it supports.

Compatibility shall be evaluated according to declared version relationships.

---

# 11. Separation of Concerns

Core answers:

> What is valid knowledge?

Runtime answers:

> How is valid knowledge managed?

Adapters answer:

> How is knowledge exposed to external systems?

Applications answer:

> What problem is being solved?

No architectural layer shall assume responsibilities belonging to another layer.

---

# 12. Architectural Invariants

The following architectural invariants are permanent constraints of the CKS ecosystem.

They shall remain valid across all future revisions of the Runtime Standard.

- CKS Core owns semantics.
- CKS Runtime owns operational lifecycle management.
- Runtime shall never modify Core semantics.
- Core shall never depend on Runtime.
- Runtime shall operate exclusively on Canonical Knowledge Structures.
- Adapters shall expose Runtime functionality without redefining Runtime behaviour.
- Applications shall consume the ecosystem without changing canonical semantics.

These invariants constitute the constitutional architecture of the CKS ecosystem.

---

# 13. Long-Term Evolution

The CKS ecosystem is intended to evolve as a family of complementary standards.

```text
CKS Standards

├── Core Standard
├── Runtime Standard
├── MCP Standard
├── CLI Standard
├── HTTP Standard
└── Conformance Suite
```

Each standard shall define a distinct architectural responsibility while preserving interoperability through the canonical semantics established by CKS Core.

---

# 14. Non-Negotiable Boundary

The following boundary shall never be violated:

```text
CKS Core
    defines meaning

          │

          ▼

CKS Runtime
    orchestrates semantic operations
    manages lifecycle

          │

          ▼

Adapters
    expose Runtime capabilities
```

Any implementation that moves semantic ownership outside CKS Core
is not a conforming CKS implementation.

---

# 15. Summary

CKS Core is the semantic foundation of the ecosystem.

CKS Runtime is the canonical execution environment built upon that foundation.

Runtime orchestrates semantic services provided by CKS Core without becoming a semantic authority itself.

The semantic authority of the CKS ecosystem permanently resides in CKS Core.

This separation enables multiple Runtime implementations, multiple transport adapters, and future ecosystem growth while preserving a single canonical definition of knowledge.