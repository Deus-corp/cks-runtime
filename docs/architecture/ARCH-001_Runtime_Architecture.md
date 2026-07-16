# ARCH-001

# Runtime Architecture

**Status:** Draft

**Applies to:** CKS Runtime Reference Architecture

**Category:** Architecture

---

# 1. Purpose

This document defines the reference architecture of a conformant
CKS Runtime implementation.

It bridges the Runtime Standard and possible Runtime implementations.

This document specifies:

- architectural responsibilities;
- component boundaries;
- ownership rules;
- dependency direction;
- interaction model.

It does not prescribe implementation details.

---

# 2. Scope

This document defines:

- Runtime components;
- architectural responsibilities;
- dependency rules;
- interaction patterns;
- implementation constraints.

This document does not define:

- storage technologies;
- programming languages;
- transport protocols;
- deployment models;
- optimisation strategies.

---

# 3. Architectural Principles

The Runtime architecture follows the principles established by the
CKS Runtime Charter.

The fundamental rules are:

- CKS Core owns semantic responsibility.
- CKS Runtime owns operational responsibility.
- Adapters expose Runtime capabilities.
- Applications consume Runtime capabilities.

Every Runtime component owns a distinct operational responsibility.

Semantic interpretation remains exclusively delegated to CKS Core.

Components communicate through explicit architectural boundaries.

---

# 3.1 Runtime Orchestration Model

Runtime is an operational orchestrator.

Runtime coordinates semantic services provided by CKS Core.

Runtime does not own semantic behaviour.

Runtime does not implement semantic behaviour.

Runtime does not duplicate semantic behaviour.

Conceptually:

CKS Core
    defines semantics

        │

        ▼

CKS Runtime
    orchestrates semantics
    manages operational lifecycle

        │

        ▼

Adapters
    expose Runtime capabilities

        │

        ▼

Applications
    consume Runtime services

---

# 4. High-Level Architecture

```text
Applications
        │
        ▼
Adapters
        │
        ▼
Runtime API Boundary
        │
        ▼
Runtime Implementation

 ┌──────────────────────────────────────┐
 │                                      │
 │        Runtime Operational Layer     │
 │                                      │
 │  Session Manager                     │
 │  Transaction Manager                 │
 │  Diagnostics Manager                 │
 │  Version Manager                     │
 │  Storage Manager                     │
 │  Explainability Coordinator          │
 │                                      │
 └──────────────────────────────────────┘

        │
        ▼

CKS Core
````

The Runtime API is the canonical operational boundary.

A Runtime implementation may expose this boundary through a Runtime
Facade or an equivalent architectural component.

---

# 5. Runtime/Core Boundary

The architectural separation between Runtime and Core is:

```text
CKS Runtime

Operational Responsibility

Sessions
Transactions
Storage
Versioning
Diagnostics
Explainability

        │

        ▼

Public CKS Core API

        │

        ▼

CKS Core

Semantic Responsibility

Knowledge Model
Validation
Constraints
Evolution
Serialization
Diagnostics
```

Runtime communicates exclusively through the public semantic API exposed by CKS Core.

Runtime shall never depend upon internal implementation details of CKS Core.

The public CKS Core API is the canonical semantic boundary between the two standards.

Runtime extends Core operationally.

Runtime never extends Core semantically.

The reference implementation provides this boundary through the
CksCoreAdapter class (part of the cks-runtime-core package).
CksCoreAdapter implements the abstract CoreInterface and
delegates every semantic call—validation, serialization, evolution,
and explainability—to the canonical cks-core library. This
adapter is the single point of contact between Runtime components
and the Core, guaranteeing that the architectural boundary remains
strictly enforced.

---

# 6. Runtime Components

The Runtime architecture consists of independent operational components.

---

## 6.1 Runtime API Boundary

The Runtime API Boundary exposes Runtime capabilities to external systems.

Responsibilities include:

* accepting Runtime operations;
* coordinating requests;
* exposing observable Runtime behaviour;
* isolating implementation details.

The API Boundary contains no semantic rules.

---

## 6.2 Session Manager

Responsible for Runtime Sessions.

Responsibilities include:

* creating Sessions;
* retrieving Sessions;
* closing Sessions;
* maintaining Session ownership;
* maintaining runtime session state.

The Session Manager owns Session lifecycle.

---

## 6.3 Transaction Manager

Responsible for Runtime Transactions.

Responsibilities include:

* creating Transactions;
* coordinating execution;
* invoking Core validation;
* determining Transaction outcomes.

Semantic validation remains delegated to CKS Core.

---

## 6.4 Diagnostics Manager

Responsible for Runtime Diagnostics.

Responsibilities include:

* collecting Runtime Diagnostics;
* aggregating Core Diagnostics;
* exposing diagnostic collections.

The Diagnostics Manager never modifies canonical diagnostics.

---

## 6.5 Version Manager

Responsible for Runtime Version History.

Responsibilities include:

* creating Runtime Versions;
* maintaining historical Runtime state;
* restoring historical Sessions.

The Version Manager owns operational history only.

---

## 6.6 Storage Manager

Responsible for Runtime persistence.

Responsibilities include:

* loading Runtime state;
* saving Runtime state;
* abstracting storage technologies.

Storage implementations remain interchangeable.

---

## 6.7 Explainability Coordinator

Responsible for explanation orchestration.

Responsibilities include:

* coordinating explanation requests;
* collecting explanation inputs;
* composing operational explanations.

Runtime shall not define semantic explanations.

Explanation sources may originate from:

* CKS Core semantics;
* Runtime state;
* diagnostics;
* future explanation providers.

---

# 7. Dependency Rules

Runtime dependencies are strictly layered.

```text
Runtime Components

↓

Core API Boundary

↓

Public CKS Core API

↓

CKS Core
```

The following rules apply:

* dependencies shall be explicit;
* dependency cycles are prohibited;
* ownership boundaries shall remain preserved;
* Runtime components shall not introduce semantic logic.

Runtime components may depend on other Runtime components when:

* the dependency is required by responsibility ownership;
* the dependency is explicit;
* the dependency remains acyclic.

Examples:

```text
Transaction Manager
        │
        ▼
Diagnostics Manager


Version Manager
        │
        ▼
Storage Manager
```

---

# 8. Component Interaction Model

Runtime execution is coordinated rather than strictly sequential.

Conceptually:

```text
                         Adapter
                            │
                            ▼
                    Runtime API Boundary
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼

   Session Manager   Transaction Manager   Diagnostics Manager

                            │
                            ▼

                         CKS Core

                            │

          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼

   Version Manager   Storage Manager   Diagnostics Manager
```

Implementations may optimise internal execution while preserving
equivalent observable behaviour.

---

# 9. Ownership Model

Each Runtime concept has exactly one owner.

| Concept                   | Owner                      |
| ------------------------- | -------------------------- |
| Runtime API               | Runtime API Boundary       |
| Session lifecycle         | Session Manager            |
| Runtime session state     | Session Manager            |
| Transactions              | Transaction Manager        |
| Transaction state         | Transaction Manager        |
| Runtime Diagnostics       | Diagnostics Manager        |
| Version History           | Version Manager            |
| Historical Runtime state  | Version Manager            |
| Persistent Runtime state  | Storage Manager            |
| Persistence abstraction   | Storage Manager            |
| Explanation orchestration | Explainability Coordinator |
| Semantic validation       | CKS Core                   |
| Canonical knowledge       | CKS Core                   |

Ownership shall remain unique.

---

# 10. Architectural Constraints

A conformant Runtime architecture shall satisfy:

* semantic behaviour shall never migrate into Runtime components;
* Runtime shall not redefine Core concepts;
* Runtime shall remain transport-independent;
* Runtime shall remain storage-independent;
* Runtime state ownership shall remain explicit;
* component responsibilities shall remain isolated.

---

# 11. Core API Boundary

Runtime implementations should isolate all communication with CKS Core through a dedicated Core API Boundary.

Conceptually:

Runtime Components

        │

Core API Boundary

        │

Public CKS Core API

        │

CKS Core

The reference implementation realises this boundary with the
cks-runtime-core package and its CksCoreAdapter class, which
wraps the public Python API of cks-core.

This boundary prevents Runtime components from depending upon internal implementation details of CKS Core.

Only the public semantic API forms the supported integration contract between the two standards.

---

# 12. Reference Package Structure

A conformant Reference Runtime may be organised conceptually as:

```text
cks_runtime/
    runtime.py
    api/
    facade/
    session/
    transaction/
    diagnostics/
    versioning/
    storage/
    explainability/
    core_api/
    cks_runtime_core/    # concrete CoreInterface implementation
```

Equivalent layouts are permitted provided architectural responsibilities
remain preserved.

---

# 13. Future Evolution

Additional Runtime components may be introduced.

Future extensions shall preserve:

* Runtime/Core separation;
* ownership boundaries;
* dependency direction;
* operational responsibility model.

New components shall not redefine existing responsibilities.

---

# 14. Relationship to Runtime Standard

This document is informative with respect to implementation architecture.

Normative Runtime behaviour remains defined by:

* SPEC-001 Runtime Overview;
* SPEC-002 Session Model;
* SPEC-003 Runtime API;
* SPEC-004 Diagnostics;
* SPEC-005 Transactions;
* SPEC-006 Storage;
* SPEC-007 Version History;
* SPEC-008 Conformance.

The Reference Runtime Architecture exists to realise these specifications
in a maintainable and implementation-independent manner.

---

# 15. Summary

The Reference Runtime architecture separates operational responsibilities
into independent components coordinated through the Runtime API Boundary.

CKS Core remains the sole authority over semantic meaning.

CKS Runtime provides the operational environment required to manage,
execute, persist and observe Canonical Knowledge Structures while preserving
the semantic guarantees established by CKS Core.
