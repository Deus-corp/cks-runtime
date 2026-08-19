"""
Integration tests: the 'claim_integrity' extension (cks-core) reaches
CksCoreAdapter.validate() through the existing 'extra_constraints'
passthrough, with no Runtime production-code changes required.
"""

from __future__ import annotations

from cks.constraints.builtin import OPTIONAL_CONSTRAINTS_BY_NAME
from cks.core import KnowledgeObject, KnowledgeStructure, ObjectIdentity

from cks_runtime.adapters.cks_core import CksCoreAdapter

CLAIM_INTEGRITY = OPTIONAL_CONSTRAINTS_BY_NAME["claim_integrity"]


def _claim(oid: str, **overrides) -> KnowledgeObject:
    structure = {
        "statement": "The Earth orbits the Sun.",
        "confidence": 0.97,
        "author": "researcher-agent",
        "created_at": "2026-08-15T00:00:00Z",
        "status": "accepted",
    }
    structure.update(overrides)
    return KnowledgeObject(
        identity=ObjectIdentity(id=oid, type="Claim", name=oid),
        structure=structure,
    )


def test_valid_claim_validates_through_runtime_when_claim_integrity_enabled():
    adapter = CksCoreAdapter()
    structure = KnowledgeStructure([_claim("c1")])

    result = adapter.validate(structure, extra_constraints=[CLAIM_INTEGRITY])

    assert result.valid is True
    assert result.diagnostics == ()


def test_malformed_claim_is_rejected_through_runtime_when_claim_integrity_enabled():
    adapter = CksCoreAdapter()
    malformed = _claim("c1", confidence=1.5, statement="")
    structure = KnowledgeStructure([malformed])

    result = adapter.validate(structure, extra_constraints=[CLAIM_INTEGRITY])

    assert result.valid is False
    codes = {d.code for d in result.diagnostics}
    assert "CKS-EXT-CLAIM-INTEGRITY" in codes


def test_malformed_claim_passes_without_claim_integrity_opted_in():
    """Sanity check: the extension is opt-in, matching every other
    OPTIONAL_CONSTRAINTS_BY_NAME extension -- a malformed Claim is
    invisible to validate() unless the caller explicitly requests it.
    """
    adapter = CksCoreAdapter()
    malformed = _claim("c1", confidence=1.5, statement="")
    structure = KnowledgeStructure([malformed])

    result = adapter.validate(structure)

    assert result.valid is True
