"""
Runtime ↔ Core Bridge.

Stable bridge between Runtime and any Core implementation.

Runtime depends only on CoreInterface.

Concrete implementations are supplied by Runtime plugins.

The bridge performs delegation and model translation only.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class CoreBridge:
    """
    Stable Runtime → Core bridge.

    The bridge:

    • owns Runtime → Core communication;

    • hides concrete plugin implementations;

    • translates Core-native objects into Runtime-native objects;

    • never contains semantic logic.
    """

    def __init__(
        self,
        implementation: CoreInterface | None = None,
    ) -> None:
        self._implementation = implementation

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def implementation(
        self,
    ) -> CoreInterface | None:
        """
        Attached Core implementation.
        """

        return self._implementation

    @property
    def available(
        self,
    ) -> bool:
        """
        Whether a Core implementation is attached.
        """

        return self._implementation is not None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        knowledge_structure: Any,
        *,
        extra_constraints: Any = None,
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure.

        ``extra_constraints`` is opaque to Runtime: it is passed
        through verbatim to whatever Core implementation is attached.
        Only forwarded as a keyword argument when actually supplied,
        so Core implementations written against the pre-existing
        ``validate(knowledge_structure)`` signature keep working
        unchanged as long as callers don't request extra constraints
        from them.
        """
        impl = self._implementation
        if impl is None:
            return RuntimeValidationResult(valid=True)

        if extra_constraints is not None:
            result = impl.validate(
                knowledge_structure,
                extra_constraints=extra_constraints,
            )
        else:
            result = impl.validate(knowledge_structure)

        if not isinstance(result, RuntimeValidationResult):
            raise TypeError(
                f"Core plugin returned {type(result).__name__}, "
                f"expected RuntimeValidationResult."
            )

        return result

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Delegate semantic evolution.
        """

        impl = self._implementation
        if impl is None:
            return knowledge_structure

        return impl.evolve(
            knowledge_structure,
            operation,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(
        self,
        knowledge_structure: Any,
    ) -> str:
        """
        Produce canonical serialization.
        """

        impl = self._implementation
        if impl is None:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )

        return impl.serialize(
            knowledge_structure,
        )

    # ------------------------------------------------------------------
    # Explainability
    # ------------------------------------------------------------------

    def explain(
        self,
        knowledge_structure: Any,
    ) -> dict[str, Any]:
        """
        Produce a semantic explanation.
        """

        impl = self._implementation
        if impl is None:
            return {}

        return impl.explain(
            knowledge_structure,
        )

    # ------------------------------------------------------------------
    # Inference explanation (optional capability)
    # ------------------------------------------------------------------

    def explain_inference(
        self,
        knowledge_structure: Any,
        object_id: str,
    ) -> dict[str, Any]:
        """
        Delegate an inference explanation ("why is object_id believed?").

        Raises
        ------
        RuntimeError
            No Core implementation is attached at all.
        NotImplementedError
            A Core implementation is attached but does not support
            inference explanation. Propagated as-is, matching
            ``field_diff()``'s contract.
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError("No Runtime Core implementation is attached.")
        return impl.explain_inference(knowledge_structure, object_id)

    @property
    def supports_explain_inference(self) -> bool:
        """
        Whether the attached Core implementation overrides
        ``explain_inference()``. Mirrors ``supports_field_diff``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return (
            type(impl).explain_inference
            is not CoreInterface.explain_inference
        )

    # ------------------------------------------------------------------
    # Structural Diff
    # ------------------------------------------------------------------

    def diff(self, source: Any, target: Any) -> list[Any]:
        impl = self._implementation
        if impl is None:
            return []
        return impl.diff(source, target)

    # ------------------------------------------------------------------
    # Field-level structural diff (optional capability)
    # ------------------------------------------------------------------

    def field_diff(self, source: Any, target: Any) -> list[Any]:
        """
        Delegate a field-granular structural diff.

        Raises
        ------
        RuntimeError
            No Core implementation is attached at all -- unlike
            ``diff()``, there is no sensible empty-list default here:
            an empty list from ``field_diff`` means "nothing changed",
            which would be actively wrong to report when there's no
            Core attached to have computed that.
        NotImplementedError
            A Core implementation is attached but does not support
            field-level diffing. Propagated as-is, matching
            ``hash()``/``merge()``'s contract.
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError("No Runtime Core implementation is attached.")
        return impl.field_diff(source, target)

    @property
    def supports_field_diff(self) -> bool:
        """
        Whether the attached Core implementation overrides
        ``field_diff()``. Mirrors ``supports_hash``/``supports_merge``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return (
            type(impl).field_diff
            is not CoreInterface.field_diff
        )

    # ------------------------------------------------------------------
    # Field-level merge synthesis (optional capability)
    # ------------------------------------------------------------------

    def synthesize_merge(self, base_object: Any, operations: list[Any]) -> Any:
        """
        Delegate field-level merge synthesis.

        Raises
        ------
        RuntimeError
            No Core implementation is attached at all.
        NotImplementedError
            A Core implementation is attached but does not support
            field-level merge synthesis. Propagated as-is, matching
            ``field_diff()``'s contract.
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError("No Runtime Core implementation is attached.")
        return impl.synthesize_merge(base_object, operations)

    @property
    def supports_synthesize_merge(self) -> bool:
        """
        Whether the attached Core implementation overrides
        ``synthesize_merge()``. Mirrors ``supports_field_diff``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return (
            type(impl).synthesize_merge
            is not CoreInterface.synthesize_merge
        )

    # ------------------------------------------------------------------
    # Three-way merge (optional capability)
    # ------------------------------------------------------------------

    def merge(
        self,
        base: Any,
        branch_a: Any,
        branch_b: Any,
        *,
        resolutions: dict[str, Any] | None = None,
    ) -> Any:
        """
        Delegate a three-way merge.

        Raises
        ------
        RuntimeError
            No Core implementation is attached at all -- unlike
            ``evolve``/``explain``/``diff``, there is no sensible
            identity-like default to fall back to for a merge of three
            structures.
        NotImplementedError
            A Core implementation is attached but does not support
            merging. Propagated as-is, matching ``hash()``'s contract,
            so callers can distinguish "no Core" from "Core doesn't
            support this".
        RuntimeMergeConflictError
            The two branches changed the same identity to different,
            irreconcilable results.
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError("No Runtime Core implementation is attached.")
        return impl.merge(
            base, branch_a, branch_b, resolutions=resolutions
        )

    @property
    def supports_merge(self) -> bool:
        """
        Whether the attached Core implementation overrides ``merge()``.

        Mirrors ``supports_hash`` -- lets callers check capability
        without a try/except when they want to fail fast instead of
        catching ``NotImplementedError``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return type(impl).merge is not CoreInterface.merge

    # ------------------------------------------------------------------
    # Subgraph query (optional capability)
    # ------------------------------------------------------------------

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
        Delegate a k-hop subgraph extraction.

        Raises
        ------
        RuntimeError
            No Core implementation is attached -- there is no
            sensible default subgraph to fall back to.
        NotImplementedError
            A Core implementation is attached but does not support
            subgraph queries. Propagated as-is, matching
            ``hash()``/``merge()``'s contract.
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )
        return impl.query_subgraph(
            knowledge_structure,
            seed_ids,
            depth,
            include_relation_types=include_relation_types,
            include_object_types=include_object_types,
            max_tokens=max_tokens,
            max_objects=max_objects,
            type_weights=type_weights,
        )

    @property
    def supports_query_subgraph(self) -> bool:
        """
        Whether the attached Core implementation overrides
        ``query_subgraph()``. Mirrors ``supports_merge``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return (
            type(impl).query_subgraph
            is not CoreInterface.query_subgraph
        )

    # ------------------------------------------------------------------
    # Content hashing (optional capability)
    # ------------------------------------------------------------------

    def hash(self, knowledge_structure: Any) -> str:
        """
        Delegate content hashing.

        Raises
        ------
        RuntimeError
            No Core implementation is attached at all.
        NotImplementedError
            A Core implementation is attached but does not support
            hashing. Propagated as-is (not swallowed) so callers can
            distinguish "no Core" from "Core doesn't support this".
        """
        impl = self._implementation
        if impl is None:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )
        return impl.hash(knowledge_structure)

    @property
    def supports_hash(self) -> bool:
        """
        Whether the attached Core implementation overrides ``hash()``.

        Lets callers check capability without a try/except when they
        want to skip integrity verification entirely instead of
        catching ``NotImplementedError``.
        """
        impl = self._implementation
        if impl is None:
            return False
        return type(impl).hash is not CoreInterface.hash