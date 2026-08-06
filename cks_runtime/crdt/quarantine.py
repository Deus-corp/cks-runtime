"""
CRDTQuarantine (ADR-013, Stage 2).

Adapted from BlackSwan's ``src/core/crdt_adapter.py::QuarantineBuffer``,
which buffered incoming genomes behind a reputation check before
admitting them into that project's CRDT. There is no reputation system
here -- CKS's trust boundary is cryptographic, not social -- so the
check this module performs instead is: does the object's structure
actually hash to the id it claims (the same leaf-hash identity
``crdt_store.object_id_for`` already relies on for G-Set deduplication),
and does it pass ``cks.validate()``? An object that fails either check
is never handed to ``CRDTStore.add_object`` at all, so a corrupted or
malicious payload can never poison the Merkle tree or a MV-Register
pointer.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from cks_runtime.crdt.crdt_store import ObjectIdentityMismatch, object_id_for

logger = logging.getLogger(__name__)


class _SupportsAddObject(Protocol):
    def add_object(self, knowledge_object: Any) -> bool: ...


class _SupportsValidate(Protocol):
    def validate(self, knowledge_structure: Any) -> Any: ...


class CRDTQuarantine:
    """
    Validates a ``cks.KnowledgeObject`` (structural validity + Merkle
    identity) before admitting it into a ``CRDTStore``'s G-Set.

    ``store`` is duck-typed the same way ``GossipAdapter`` treats its
    own ``crdt_store`` -- any of ``SQLiteCRDTStore``/
    ``PostgresCRDTStore``/``InMemoryCRDTStore`` works, sync or async
    ``add_object`` alike (see ``validate_and_add``'s ``await``
    handling). ``cks`` is the ``cks-core`` engine/adapter used to
    validate structural correctness -- typically a ``CksCoreAdapter``
    instance, matching what the rest of the runtime already passes to
    ``Runtime.create(core=...)``.
    """

    def __init__(self, store: _SupportsAddObject, cks: _SupportsValidate) -> None:
        self._store = store
        self._cks = cks

    async def validate_and_add(self, knowledge_object: Any) -> bool:
        """
        Validate ``knowledge_object`` and, if it passes, add it to the
        underlying store. Returns True iff both validation and the
        underlying ``add_object`` succeeded (the latter returning
        False for an object already known is *not* a validation
        failure -- it is reported as False here too, since nothing new
        was actually admitted, but for a different reason than a
        rejected object; callers that need to distinguish "invalid"
        from "already present" should validate separately).
        """
        if not self._is_structurally_valid(knowledge_object):
            logger.warning(
                "CRDTQuarantine: rejecting object that failed cks.validate()"
            )
            return False

        if not self._has_consistent_identity(knowledge_object):
            logger.warning(
                "CRDTQuarantine: rejecting object with a Merkle-hash/identity mismatch"
            )
            return False

        result = self._store.add_object(knowledge_object)
        if hasattr(result, "__await__"):
            result = await result
        return bool(result)

    async def process_batch(self, objects: list[Any]) -> int:
        """Validate and add every object in ``objects``; return the count admitted."""
        admitted = 0
        for obj in objects:
            if await self.validate_and_add(obj):
                admitted += 1
        return admitted

    # -- internal checks ------------------------------------------------

    def _is_structurally_valid(self, knowledge_object: Any) -> bool:
        validate = getattr(self._cks, "validate", None)
        if not callable(validate):
            # No validator wired up -- fail open on structural checks
            # rather than reject everything; the identity check below
            # still applies unconditionally.
            return True
        try:
            # ``validate`` (e.g. ``CoreBridge.validate``, the usual
            # ``cks`` passed in here -- see the class docstring)
            # validates a *KnowledgeStructure*, not a bare
            # KnowledgeObject -- there is no single-object validation
            # entry point in the Core interface. Wrap the incoming
            # object in a throwaway single-object structure so the
            # real validator (structural invariants, cross-object
            # constraints that happen to degenerate sensibly for one
            # object, etc.) actually runs, instead of being handed a
            # shape it was never written to accept. A dict payload
            # (no live cks.KnowledgeObject to wrap) skips this step --
            # it isn't something cks.KnowledgeStructure can hold to
            # begin with -- and relies on the identity check below
            # alone; that's an accepted, narrower gap for that input
            # shape, not a regression versus before this change.
            structure_input = knowledge_object
            if not isinstance(knowledge_object, dict) and hasattr(
                knowledge_object, "identity"
            ):
                import cks

                structure_input = cks.KnowledgeStructure(objects=[knowledge_object])
            result = validate(structure_input)
        except Exception:
            logger.exception("CRDTQuarantine: cks.validate() raised")
            return False

        # Mirror the shape `evolve_knowledge`/`validate_knowledge`
        # already use elsewhere in this codebase: a falsy return, or a
        # result exposing `.is_valid` / `.valid` that is falsy, means
        # rejected. Anything else (including a bare truthy return, or
        # a result type with neither attribute) is treated as valid.
        if isinstance(result, bool):
            return result
        for attr in ("is_valid", "valid"):
            if hasattr(result, attr):
                return bool(getattr(result, attr))
        return True

    def _has_consistent_identity(self, knowledge_object: Any) -> bool:
        try:
            object_id_for(knowledge_object)
        except (TypeError, ObjectIdentityMismatch):
            return False
        # object_id_for now verifies -- not just derives -- the id for
        # both input shapes: for a live cks.KnowledgeObject it reads
        # the object's own Merkle leaf hash (`_hash`) directly, and
        # for a dict payload (e.g. a gossip-carried record) it
        # recomputes that same hash from the dict's own
        # identity/structure fields and raises ObjectIdentityMismatch
        # if the claimed 'id' doesn't match. So successfully computing
        # it at all is now genuinely the identity check for either
        # shape -- there is no path left where a caller-controlled
        # "id" key is trusted without being checked against the
        # content it's supposed to describe.
        return True