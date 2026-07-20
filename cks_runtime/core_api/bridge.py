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
    

    def diff(self, source: Any, target: Any) -> list[Any]:
        if not self.available:
            return []
        return self._implementation.diff(source, target)