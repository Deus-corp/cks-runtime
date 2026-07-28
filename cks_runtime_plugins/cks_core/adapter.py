"""
CKS Runtime – CKS Core Adapter.

Concrete implementation of the Runtime CoreInterface using
the canonical `cks-core` library.

This adapter is the only place that knows how to translate
between CKS Core native objects and Runtime abstractions.
"""

from __future__ import annotations

from typing import Any

import cks
from cks.diagnostics import DiagnosticSeverity as CoreSeverity
from cks.evolution import compose
from cks.interface import inspect as cks_inspect

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.merge_conflict import (
    RuntimeMergeConflict,
    RuntimeMergeConflictError,
)
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.diagnostics.diagnostic import (
    Diagnostic as RuntimeDiagnostic,
)
from cks_runtime.diagnostics.diagnostic import (
    DiagnosticSeverity as RuntimeSeverity,
)
from cks_runtime.diagnostics.diagnostic import (
    DiagnosticSource,
)

_SEVERITY_MAP = {
    CoreSeverity.INFORMATION: RuntimeSeverity.INFO,
    CoreSeverity.WARNING: RuntimeSeverity.WARNING,
    CoreSeverity.ERROR: RuntimeSeverity.ERROR,
}

# Sentinel for "this structure key is absent", distinct from any
# real value a key could hold (including None). Used by field_diff()
# to tell a deleted key apart from a key explicitly set to None.
_MISSING = object()


def _translate_diagnostic(diagnostic: Any) -> RuntimeDiagnostic:
    """
    Translate a cks-core Diagnostic into a Runtime-native Diagnostic.

    cks-core diagnostics freeze ``metadata`` into a MappingProxyType,
    which the stdlib ``copy`` module cannot deepcopy. Runtime persists
    Diagnostics via deepcopy (see InMemoryStorage), so foreign
    cks-core Diagnostic instances must never be stored as-is -- they
    are always translated into the Runtime's own, deepcopy-safe
    Diagnostic type at this boundary.
    """

    metadata = dict(diagnostic.metadata)
    if diagnostic.location is not None:
        metadata.setdefault("location", diagnostic.location)

    return RuntimeDiagnostic(
        message=diagnostic.message,
        source=DiagnosticSource.CORE,
        severity=_SEVERITY_MAP[diagnostic.severity],
        code=diagnostic.identity,
        metadata=metadata,
    )


class CksCoreAdapter(CoreInterface):
    """
    Concrete Runtime → CKS Core adapter.

    All semantic behaviour is delegated to cks-core.

    Runtime never communicates with cks-core directly.
    """

    def validate(
        self,
        knowledge_structure: Any,
        *,
        extra_constraints: Any = None,
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure using CKS Core.
        """

        result = cks.validate(
            knowledge_structure,
            extra_constraints=extra_constraints,
        )

        return RuntimeValidationResult(
            valid=result.is_valid,
            diagnostics=tuple(
                _translate_diagnostic(d) for d in result.diagnostics
            ),
            metadata=dict(
                result.metadata,
            ),
        )

    def serialize(
        self,
        knowledge_structure: Any,
    ) -> str:
        """
        Serialize a Knowledge Structure into its canonical form.
        """

        return cks.serialize(
            knowledge_structure,
        )

    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Apply semantic evolution through CKS Core.
        """

        if not isinstance(
            operation,
            (list, tuple),
        ):
            raise TypeError(
                "Evolution operation must be a sequence of operators."
            )

        return compose(
            knowledge_structure,
            operation,
        )

    def explain(
        self,
        knowledge_structure: Any,
    ) -> dict[str, Any]:
        """
        Produce a semantic explanation for a Knowledge Structure.
        """

        summary = cks_inspect(
            knowledge_structure,
        )

        return {
            "object_count": len(
                knowledge_structure.objects,
            ),
            "relation_count": len(
                knowledge_structure.relations(),
            ),
            "summary": summary,
        }

    def diff(self, source: Any, target: Any) -> list[Any]:
        return source.diff(target)

    def field_diff(self, source: Any, target: Any) -> list[RuntimeFieldOperation]:
        """
        Field-granular diff between two KnowledgeStructures.

        Unlike ``diff()`` (``source.diff(target)``), which reports any
        content change to an existing identity as a
        RemoveObject+AddObject pair -- deliberately discarding which
        fields actually changed, so that ``cks.evolution.compose``
        never has to reason about partially-applied objects -- this
        walks every identity present in both structures and reports
        exactly which ``structure`` keys differ. This is what lets
        Runtime's operation log (ADR-007) tell "two branches edited
        different fields of the same object" apart from "two branches
        edited the same field", which a whole-object diff cannot.

        Relations have no granular update in cks-core (``UpdateObject``
        explicitly rejects a ``CanonicalRelation`` target: see its
        docstring), so a changed relation is still reported as a
        remove+add pair here, exactly as ``diff()`` would report it --
        there is no narrower operation to name.
        """
        from cks.core import CanonicalRelation

        source_ids = {obj.identity.id for obj in source.objects}
        target_ids = {obj.identity.id for obj in target.objects}

        added_ids = target_ids - source_ids
        removed_ids = source_ids - target_ids
        common_ids = source_ids & target_ids

        operations: list[RuntimeFieldOperation] = []

        for oid in sorted(removed_ids):
            op_type = (
                "remove_relation"
                if isinstance(source.get(oid), CanonicalRelation)
                else "remove_object"
            )
            operations.append(RuntimeFieldOperation(object_id=oid, op_type=op_type))

        for oid in sorted(added_ids):
            op_type = (
                "add_relation"
                if isinstance(target.get(oid), CanonicalRelation)
                else "add_object"
            )
            operations.append(RuntimeFieldOperation(object_id=oid, op_type=op_type))

        for oid in sorted(common_ids):
            source_obj = source.get(oid)
            target_obj = target.get(oid)
            source_structure = dict(source_obj.structure)
            target_structure = dict(target_obj.structure)

            if source_structure == target_structure:
                continue  # untouched -- carried over unchanged

            if isinstance(source_obj, CanonicalRelation) or isinstance(
                target_obj, CanonicalRelation
            ):
                operations.append(
                    RuntimeFieldOperation(object_id=oid, op_type="remove_relation")
                )
                operations.append(
                    RuntimeFieldOperation(object_id=oid, op_type="add_relation")
                )
                continue

            for key in sorted(set(source_structure) | set(target_structure)):
                # A sentinel, not dict.get(key)'s implicit None
                # default: a key can legitimately hold the value
                # None, and that must stay distinguishable from the
                # key being absent altogether. Using None as the
                # "missing" marker here would make an added-with-null
                # key look unchanged (None == None) and a
                # deleted-key look like "set to None" -- exactly the
                # ambiguity delete_field exists to avoid.
                source_val = source_structure.get(key, _MISSING)
                target_val = target_structure.get(key, _MISSING)

                if target_val is _MISSING:
                    # Present in source, gone in target: deleted,
                    # regardless of what value it used to hold.
                    operations.append(
                        RuntimeFieldOperation(
                            object_id=oid, op_type="delete_field", field_key=key
                        )
                    )
                    continue

                if source_val != target_val:
                    # Covers both a changed value and a key that's
                    # new in target (source_val is _MISSING), field_value
                    # is target's real value either way -- including
                    # a literal None for a newly-added null field.
                    operations.append(
                        RuntimeFieldOperation(
                            object_id=oid,
                            op_type="set_field",
                            field_key=key,
                            field_value=target_val,
                        )
                    )

        return operations

    def synthesize_merge(
        self, base_object: Any, operations: list[RuntimeFieldOperation]
    ) -> Any:
        """
        Build a merged KnowledgeObject by applying a set of
        non-conflicting field-level operations on top of
        ``base_object``.

        This is the write-side counterpart to ``field_diff()``: given
        an object as it existed at the merge base, and a combined
        list of ``set_field``/``delete_field`` operations logged by
        two branches for that object_id (already checked by the
        caller -- typically ``MergeOperation`` -- to touch disjoint
        ``field_key``s), reconstructs what the object should look
        like with both branches' changes applied together.

        Deliberately does not go through ``UpdateObject(mode="merge")``
        directly: that mode treats any patch value of ``None`` as
        "delete this key" (see its docstring), which would silently
        misapply a ``set_field`` operation whose real field_value is
        a literal ``None`` -- exactly the ambiguity ``delete_field``
        exists to keep separate from ``set_field``. Instead, the new
        ``structure`` dict is built by hand, where ``set_field`` and
        ``delete_field`` are unambiguous, and committed via
        ``UpdateObject(mode="replace")``, which takes it verbatim.

        Only plain objects are supported: relations have no
        granular-update operator in cks-core (``UpdateObject`` itself
        rejects a ``CanonicalRelation`` target), and ``field_diff()``
        never emits ``set_field``/``delete_field`` for one, so a
        conflict on a relation should never reach this method -- the
        caller's own op_type check already excludes it.

        Raises
        ------
        ValueError
            ``base_object`` is ``None`` (there is nothing to apply
            the patch to -- the caller should not attempt a
            field-level merge for an identity absent from the base),
            or ``operations`` contains an object_id other than
            ``base_object``'s, or an op_type other than
            ``set_field``/``delete_field``.
        """
        from cks.core import KnowledgeStructure
        from cks.evolution import UpdateObject

        if base_object is None:
            raise ValueError(
                "synthesize_merge requires a base_object; an identity "
                "absent from the merge base has no field-level patch "
                "to apply -- it was added independently by both "
                "branches, which field_diff() reports as add_object, "
                "not set_field/delete_field."
            )

        object_id = base_object.identity.id
        new_structure = dict(base_object.structure)

        for op in operations:
            if op.object_id != object_id:
                raise ValueError(
                    f"synthesize_merge got an operation for "
                    f"'{op.object_id}' while merging '{object_id}'."
                )
            if op.op_type == "delete_field":
                new_structure.pop(op.field_key, None)
            elif op.op_type == "set_field":
                new_structure[op.field_key] = op.field_value
            else:
                raise ValueError(
                    f"synthesize_merge only supports set_field/"
                    f"delete_field operations, got {op.op_type!r} "
                    f"for '{object_id}'."
                )

        wrapper = KnowledgeStructure([base_object])
        updated = UpdateObject(object_id, new_structure, mode="replace").apply(
            wrapper
        )
        return updated.get(object_id)

    def merge(
        self,
        base: Any,
        branch_a: Any,
        branch_b: Any,
        *,
        resolutions: dict[str, Any] | None = None,
    ) -> Any:
        """
        Three-way merge through CKS Core.

        cks-core's own ``MergeConflictError`` is a Core-native
        exception (it carries ``cks.MergeConflict`` instances holding
        raw ``KnowledgeObject``/``CanonicalRelation`` values) -- it is
        translated into the Runtime-native ``RuntimeMergeConflictError``
        here, at the same adapter boundary that ``_translate_diagnostic``
        already uses for validation diagnostics, so Runtime code never
        needs to import or recognize a cks-core-specific exception
        type.

        ``resolutions`` (optional) turns this into a partial merge --
        see ``cks.merge``/``KnowledgeStructure.merge`` for the exact
        semantics. It is passed straight through unmodified: values
        are either the strings ``"branch_a"``/``"branch_b"``, ``None``,
        or a raw cks-core ``KnowledgeObject``/``CanonicalRelation``
        instance the caller has already constructed.
        """

        try:
            return cks.merge(base, branch_a, branch_b, resolutions=resolutions)
        except cks.MergeConflictError as exc:
            raise RuntimeMergeConflictError(
                [
                    RuntimeMergeConflict(
                        object_id=conflict.object_id,
                        base=conflict.base,
                        branch_a=conflict.branch_a,
                        branch_b=conflict.branch_b,
                    )
                    for conflict in exc.conflicts
                ]
            ) from exc

    def query_subgraph(
        self,
        knowledge_structure: Any,
        seed_ids: Any,
        depth: int = 1,
        *,
        include_relation_types: Any = None,
        include_object_types: Any = None,
        max_tokens: int | None = None,
        max_objects: int | None = None,
        type_weights: Any = None,
    ) -> Any:
        """
        k-hop subgraph extraction through CKS Core.

        Unlike ``merge()``, there is no Core-native exception to
        translate here: cks-core's ``query_subgraph`` never raises for
        an unmatched seed (it returns an empty result instead), so
        this is a plain passthrough, the same shape as ``diff()``
        above.
        """
        return cks.query_subgraph(
            knowledge_structure,
            seed_ids,
            depth,
            include_relation_types=include_relation_types,
            include_object_types=include_object_types,
            max_tokens=max_tokens,
            max_objects=max_objects,
            type_weights=type_weights,
        )

    def hash(self, knowledge_structure: Any) -> str:
        return knowledge_structure.root_hash