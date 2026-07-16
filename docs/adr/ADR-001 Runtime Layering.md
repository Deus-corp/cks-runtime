# ADR-001

# Runtime Layering

**Status:** Accepted

**Date:** 2026-07-15

**Category:** Architecture Decision Record

---

# Context

The CKS ecosystem consists of multiple architectural concerns.

Early prototypes combined semantic behaviour, runtime lifecycle management
and external transport logic within a single implementation.

As the project evolved, this approach became increasingly difficult to
maintain, extend and standardize.

A stable architectural separation was required.

---

# Problem

The ecosystem requires:

- a single canonical semantic model;
- independent Runtime implementations;
- multiple transport adapters;
- implementation independence;
- long-term ecosystem evolution.

Without explicit architectural boundaries, semantic and operational
responsibilities would become tightly coupled.

---

# Decision

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

The reference implementation realises this layering through the
`CoreInterface` abstraction (defined in `cks_runtime.core_api.interfaces`)
and its concrete implementation, `CksCoreAdapter` (part of the
`cks-runtime-core` package).

Detailed Runtime architecture is defined separately by ARCH-001.

Each layer owns a clearly defined architectural responsibility and shall
not redefine responsibilities belonging to another layer.

---

# Responsibilities

## CKS Core

CKS Core owns semantic behaviour.

Core responsibilities include:

* canonical knowledge model;
* object and relation semantics;
* canonical identities;
* validation;
* constraints;
* diagnostics model;
* serialization;
* semantic evolution.

CKS Core defines what knowledge means.

---

## CKS Runtime

CKS Runtime owns operational behaviour.

Runtime responsibilities include:

* sessions;
* lifecycle management;
* transactions;
* runtime state management;
* storage abstraction;
* version history;
* diagnostics aggregation;
* explainability orchestration.

CKS Runtime defines how knowledge is managed.

---

## Adapters

Adapters expose Runtime capabilities to external systems.

Examples include:

* MCP;
* CLI;
* HTTP;
* Python API.

Adapters are responsible for transport and integration concerns.

Adapters shall not bypass Runtime operational boundaries or redefine
semantic behaviour.

---

## Applications

Applications consume Runtime services.

Applications contain domain-specific behaviour and workflows.

Applications shall not redefine Core semantics.

---

# Dependency Rules

Dependencies shall always follow this direction.

```text
Applications

↓

Adapters

↓

Runtime

↓

Core
```

These rules are reflected in the project's `pyproject.toml`: the
`cks-runtime` package declares `cks-core` as a dependency, while
`cks-core` has no dependency on `cks-runtime`.

The following rules are mandatory:

* Core shall never depend on Runtime.
* Runtime may depend on Core.
* Adapters may depend on Runtime.
* Applications may depend on lower layers.

Reverse dependencies are prohibited.

---

# Relationship to CKS Core

CKS Runtime is architecturally dependent on CKS Core.

CKS Core remains the sole semantic authority.

Runtime may invoke:

* canonical validation;
* canonical evolution;
* canonical serialization;
* canonical diagnostics.

Runtime shall never modify, replace or reinterpret Core semantics.

---

# Consequences

Positive consequences include:

* clear separation of responsibilities;
* independent evolution of Core and Runtime;
* multiple Runtime implementations;
* multiple transport adapters;
* simplified testing;
* improved maintainability;
* stable semantic compatibility.

The introduction of the `cks-runtime-core` package with
`CksCoreAdapter` is a direct consequence of this decision: it
provides a single, swappable integration point between the
operational and semantic layers.

Negative consequences include:

* additional architectural layers;
* explicit interface boundaries;
* increased specification overhead.

These trade-offs are considered acceptable.

---

# Alternatives Considered

## Monolithic Architecture

A single implementation containing Core, Runtime and adapters.

Rejected because semantic and operational responsibilities become tightly
coupled.

---

## Adapter-Centric Architecture

Adapters communicate directly with CKS Core.

Rejected because lifecycle management and operational behaviour would be
duplicated across adapters.

---

## Runtime-Embedded Semantics

Moving validation or semantic behaviour into Runtime.

Rejected because semantic authority must remain exclusively within CKS Core.

---

# Rationale

Separating Core and Runtime preserves the long-term stability of semantic
standards while allowing Runtime implementations to evolve independently.

This architecture enables multiple transport adapters, execution
environments and storage implementations without duplicating semantic
logic.

---

# Status

Accepted.

Future Runtime specifications, architectures and implementations shall
preserve this architectural layering.
