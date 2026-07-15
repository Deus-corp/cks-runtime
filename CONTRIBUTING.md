# Contributing to CKS Runtime

Thank you for your interest in contributing to the CKS Runtime project.

CKS Runtime is the canonical operational environment for Canonical Knowledge Structures.

Its purpose is to provide lifecycle management, transactions, storage, version history, diagnostics, and adapter integration while preserving the semantic guarantees established by CKS Core.

---

# Guiding Principles

All contributions should preserve the architectural principles of the CKS ecosystem.

In particular:

* Runtime/Core Separation
* Operational Determinism
* Storage Independence
* Transport Independence
* Adapter Independence
* Semantic Authority of CKS Core

Runtime manages operational behaviour.

CKS Core defines semantic behaviour.

---

# Types of Contributions

Contributions are welcome in several areas, including:

* Runtime specifications
* Runtime architecture
* Runtime implementation
* Session management
* Transaction management
* Storage providers
* Version history
* Diagnostics
* Explainability
* Runtime adapters
* Documentation
* Tests
* Bug reports
* Design discussions

---

# Before Contributing

Before implementing a significant change, please:

1. Review the Runtime specifications.
2. Review the Runtime architecture documents.
3. Review the Runtime Charter.
4. Search existing Issues and Discussions.
5. Open a discussion before proposing major architectural changes.

Large architectural changes should be discussed before implementation.

---

# Development Guidelines

The Runtime reference implementation emphasizes simplicity, readability, and architectural correctness.

Contributors are encouraged to:

* write clear and explicit code;
* keep components focused;
* preserve deterministic behaviour;
* avoid unnecessary dependencies;
* maintain Runtime/Core separation;
* preserve implementation independence.

Whenever practical, new functionality should include corresponding unit tests.

---

# Architectural Rule

CKS Runtime shall never become a second semantic engine.

In particular, Runtime shall never:

* implement semantic validation;
* redefine canonical identities;
* reinterpret diagnostics;
* introduce semantic rules;
* modify Canonical Knowledge Structures directly.

Semantic behaviour always belongs to CKS Core.

Runtime coordinates operational execution only.

---

# Coding Style

The Python reference implementation follows modern Python practices:

* Python 3.11+
* type annotations
* explicit ownership
* immutable models where appropriate
* descriptive docstrings
* deterministic behaviour

Consistency is preferred over personal style.

---

# Commit Messages

Please use concise, descriptive commit messages.

Examples:

* Add Version Manager
* Improve Session lifecycle
* Refactor Transaction Manager
* Add Storage abstraction
* Update Runtime Architecture

---

# Pull Requests

A good pull request should:

* address a single logical change;
* include relevant tests;
* update documentation when public behaviour changes;
* preserve Runtime/Core separation;
* preserve backward compatibility unless a specification revision requires otherwise.

---

# Reporting Issues

When reporting a bug, please include:

* Python version;
* operating system;
* Runtime version;
* CKS Core version;
* steps to reproduce;
* expected behaviour;
* observed behaviour;
* complete traceback (if applicable).

---

# Relationship to CKS Core

CKS Runtime depends on CKS Core.

CKS Core never depends on Runtime.

If implementation behaviour appears to conflict with CKS Core semantics, the CKS Core specification takes precedence.

Runtime exists to operationalize Core—not to replace it.

---

# Community

CKS Runtime is intended to be an open, collaborative, and respectful project.

Constructive feedback, thoughtful discussion, and well-reasoned architectural proposals are always appreciated.

Thank you for helping improve the CKS Runtime project.