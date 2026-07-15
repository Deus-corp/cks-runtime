# ADR-006

# Adapter Architecture

**Status:** Accepted

**Date:** 2026-07-15

**Decision Type:** Architectural Boundary

---

# 1. Context

The CKS ecosystem consists of multiple external integration points:

- MCP adapters;
- CLI interfaces;
- Python APIs;
- HTTP APIs;
- future transport mechanisms.

Without an explicit architectural boundary, adapters may gradually
introduce semantic logic and duplicate responsibilities already owned
by CKS Core and CKS Runtime.

This ADR defines the architectural role, ownership and limitations of
Adapters within the CKS ecosystem.

---

# 2. Decision

Adapters are translation boundaries between external systems and
CKS Runtime.

Adapters expose Runtime capabilities through external protocols while
preserving:

- the semantic authority of CKS Core;
- the operational authority of CKS Runtime.

The dependency direction is:

```text
Applications

        ↓

Adapters

        ↓

Runtime API

        ↓

CKS Runtime

        ↓

CKS Core
````

Reverse dependencies are prohibited.

---

# 3. Architectural Roles

The CKS ecosystem assigns explicit authority boundaries.

```text
CKS Core

    Semantic Authority


CKS Runtime

    Operational Authority


Adapters

    Exposure Boundary
```

Each layer owns a distinct responsibility.

No layer shall assume responsibilities belonging to another layer.

---

# 4. Adapter Responsibilities

Adapters are responsible for:

* translating external requests into Runtime API operations;
* translating Runtime API responses into external representations;
* managing protocol-specific concerns;
* handling transport-specific failures;
* exposing Runtime capabilities through external interfaces.

Adapters perform representation translation only.

Adapters do not own execution lifecycle.

---

# 5. Allowed Translation

Adapters may perform protocol and representation translation.

Examples:

```text
HTTP Request

        ↓

Runtime API Request
```

```text
CLI Arguments

        ↓

Runtime API Operation
```

```text
MCP Tool Call

        ↓

Runtime API Invocation
```

Such translation changes representation but preserves meaning.

---

# 6. Forbidden Semantic Translation

Adapters shall never introduce alternative semantic models.

Adapters shall never transform:

```text
External Representation

        ↓

New Knowledge Model
```

Adapters shall never:

* define knowledge semantics;
* reinterpret canonical objects;
* replace Core validation;
* introduce alternative constraints;
* modify canonical identities.

Semantic authority remains exclusively with CKS Core.

---

# 7. Runtime API Boundary

The Runtime API is the only supported interaction boundary between
Adapters and Runtime.

Adapters shall:

* invoke Runtime API operations;
* preserve Runtime operation meaning;
* preserve Runtime responses;
* preserve Runtime diagnostics.

Adapters shall never access internal Runtime components directly.

The following is prohibited:

```text
Adapter

        ↓

Internal Runtime Component
```

The canonical model is:

```text
Adapter

        ↓

Runtime API

        ↓

Runtime Components
```

---

# 8. Adapter Ownership Rules

Adapters do not own Runtime lifecycle.

Adapters may request:

* Session creation;
* Runtime operations;
* Transaction execution;
* diagnostics retrieval.

Adapters shall not:

* own Sessions;
* manage Transaction lifecycle;
* persist Runtime state;
* maintain Version History.

Operational ownership remains with CKS Runtime.

---

# 9. Forbidden Adapter Responsibilities

Adapters shall never:

* define knowledge semantics;
* implement validation rules;
* modify canonical diagnostics;
* implement transaction semantics;
* manage Runtime Sessions directly;
* bypass Runtime APIs;
* introduce alternative knowledge representations;
* redefine Runtime behavior.

---

# 10. Adapter Independence

Each Adapter is independent from other Adapters.

Examples include:

```text
        MCP Adapter

              │

        Runtime API

              │

        CLI Adapter

              │

        Runtime API

              │

        HTTP Adapter
```

Multiple Adapters may expose identical Runtime capabilities without
sharing implementation details.

---

# 11. Transport Independence

Runtime behavior shall remain identical regardless of Adapter type.

The following are equivalent when translated into the same Runtime API
operation:

```text
MCP Request

CLI Command

HTTP Request

Python API Call
```

Transport differences shall not affect:

* Runtime semantics;
* Transaction outcomes;
* diagnostics meaning;
* Version History behavior.

---

# 12. Error and Diagnostic Boundaries

The ecosystem distinguishes three categories of information.

## Adapter Errors

Describe transport and protocol failures.

Examples:

* malformed HTTP request;
* invalid CLI arguments;
* unavailable external transport.

---

## Runtime Errors

Describe operational failures.

Examples:

* unavailable storage;
* invalid Session state;
* failed Runtime operation.

---

## Core Diagnostics

Describe semantic validity.

Examples:

* validation failures;
* constraint violations;
* semantic inconsistencies.

These categories shall remain distinguishable.

Adapters shall not merge or reinterpret them.

---

# 13. Semantic Preservation

Adapters shall preserve:

* Runtime operation meaning;
* Runtime diagnostics;
* Core validation results;
* canonical identities;
* transaction outcomes.

Adapters may transform representation.

Adapters shall never transform meaning.

---

# 14. Extension Model

New Adapters may be introduced without modifying:

* CKS Core;
* Runtime specifications;
* existing Adapters.

Adding a new Adapter is an ecosystem extension rather than a semantic
extension.

---

# 15. Consequences

## Positive

* clear architectural boundaries;
* multiple interchangeable interfaces;
* stable Runtime evolution;
* prevention of semantic duplication;
* transport-independent ecosystem growth.

---

## Negative

* Adapters require Runtime availability;
* direct Core integration is intentionally prohibited;
* additional translation layers are introduced.

These trade-offs are considered acceptable.

---

# 16. Alternatives Considered

## Alternative A — Each Adapter Implements Core Logic

Rejected.

This creates semantic duplication and inconsistent behavior.

---

## Alternative B — Runtime Contains Transport Implementations

Rejected.

Runtime must remain transport-independent.

---

## Alternative C — Adapters Communicate Directly With Core

Rejected.

This bypasses Runtime lifecycle management and breaks architectural
layering.

---

# 17. Final Decision

Adapters are external translation layers above CKS Runtime.

Runtime remains the canonical operational boundary.

CKS Core remains the canonical semantic authority.

The Adapter layer exists only to expose Runtime capabilities without
changing their meaning.

Every conformant Adapter implementation shall preserve the separation
between:

* external representation;
* Runtime operations;
* Core semantics.
