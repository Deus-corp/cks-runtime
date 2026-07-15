# Security Policy

## Supported Versions

The current CKS Runtime reference implementation is under active development.

Security fixes are provided for the latest released version.

| Version          | Supported |
| ---------------- | --------- |
| 0.1.x            | ✅        |
| Earlier versions | ❌        |

---

## Reporting a Vulnerability

If you discover a security vulnerability in CKS Runtime, please report it responsibly.

Please avoid creating a public GitHub issue for vulnerabilities that could affect users before they are investigated.

Instead, contact the project maintainer directly through GitHub.

When reporting a vulnerability, please include:

- a description of the issue;
- affected version;
- reproduction steps;
- expected behavior;
- actual behavior;
- any proof-of-concept if available.

---

## Response Process

The project aims to:

1. acknowledge reports promptly;
2. investigate the reported issue;
3. determine severity and impact;
4. prepare an appropriate fix;
5. publish the fix in a future release;
6. acknowledge the reporter when appropriate.

---

## Scope

At the current stage of development, the primary Runtime security considerations are:

- Session lifecycle isolation;
- Transaction integrity;
- Version immutability;
- Storage safety;
- Runtime state recovery;
- adapter boundary isolation;
- deterministic Runtime behaviour.

CKS Runtime does not define knowledge semantics.

Semantic validation remains the responsibility of CKS Core.

---

## Responsible Disclosure

Please allow reasonable time for investigation and remediation before publicly disclosing security vulnerabilities.

Responsible disclosure helps protect users while fixes are prepared.

---

## Security Philosophy

CKS Runtime follows several architectural principles that naturally support secure implementations:

- immutable Runtime Versions;
- deterministic Runtime execution;
- explicit Transaction boundaries;
- isolated Runtime Sessions;
- storage abstraction;
- transport independence;
- strict separation between operational and semantic responsibilities.

CKS Runtime intentionally delegates all semantic validation to CKS Core and never reinterprets canonical knowledge.

These principles reduce implementation risk and preserve predictable Runtime behaviour, while not eliminating the need for ongoing security review.