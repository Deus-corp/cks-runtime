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
        if not self.available:
            return RuntimeValidationResult(valid=True)

        if extra_constraints is not None:
            result = self._implementation.validate(
                knowledge_structure,
                extra_constraints=extra_constraints,
            )
        else:
            result = self._implementation.validate(knowledge_structure)

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

        if not self.available:
            return knowledge_structure

        return self._implementation.evolve(
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

        if not self.available:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )

        return self._implementation.serialize(
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

        if not self.available:
            return {}

        return self._implementation.explain(
            knowledge_structure,
        )

    # ------------------------------------------------------------------
    # Structural Diff
    # ------------------------------------------------------------------

    def diff(self, source: Any, target: Any) -> list[Any]:
        if not self.available:
            return []
        return self._implementation.diff(source, target)

    # ------------------------------------------------------------------
    # Three-way merge (optional capability)
    # ------------------------------------------------------------------

    def merge(self, base: Any, branch_a: Any, branch_b: Any) -> Any:
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
        if not self.available:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )
        return self._implementation.merge(base, branch_a, branch_b)

    @property
    def supports_merge(self) -> bool:
        """
        Whether the attached Core implementation overrides ``merge()``.

        Mirrors ``supports_hash`` -- lets callers check capability
        without a try/except when they want to fail fast instead of
        catching ``NotImplementedError``.
        """
        if not self.available:
            return False
        return type(self._implementation).merge is not CoreInterface.merge

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
        if not self.available:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )
        return self._implementation.query_subgraph(
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
        if not self.available:
            return False
        return (
            type(self._implementation).query_subgraph
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
        if not self.available:
            raise RuntimeError(
                "No Runtime Core implementation is attached."
            )
        return self._implementation.hash(knowledge_structure)

    @property
    def supports_hash(self) -> bool:
        """
        Whether the attached Core implementation overrides ``hash()``.

        Lets callers check capability without a try/except when they
        want to skip integrity verification entirely instead of
        catching ``NotImplementedError``.
        """
        if not self.available:
            return False
        return type(self._implementation).hash is not CoreInterface.hash