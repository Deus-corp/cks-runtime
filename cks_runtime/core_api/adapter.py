"""
Runtime Core Adapter.

The Core Adapter is the exclusive Runtime gateway
to CKS Core.

Runtime never communicates with CKS Core directly.

The adapter isolates Runtime from future changes
to the Core API.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class CoreAdapter:
    """
    Runtime adapter over CKS Core.

    Owns Runtime → Core interaction.

    Never owns semantic behaviour.
    """

    def __init__(
        self,
        core: CoreInterface | None,
    ) -> None:

        self._core = core

    @property
    def attached(self) -> bool:
        """
        Whether a Core implementation is attached.
        """

        return self._core is not None

    #
    # Validation
    #

    def validate(
        self,
        knowledge_structure: Any,
    ) -> RuntimeValidationResult:
        """
        Delegate validation to CKS Core.

        Runtime never receives Core-native
        ValidationResult objects.

        They are translated into
        RuntimeValidationResult.
        """

        if self._core is None:

            return RuntimeValidationResult(
                valid=True,
            )

        result = self._core.validate(
            knowledge_structure,
        )

        return RuntimeValidationResult(
            valid=getattr(
                result,
                "is_valid",
                True,
            ),
            diagnostics=tuple(
                getattr(
                    result,
                    "diagnostics",
                    (),
                )
            ),
            metadata=dict(
                getattr(
                    result,
                    "metadata",
                    {},
                )
            ),
        )

    #
    # Evolution
    #

    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Delegate semantic evolution to CKS Core.
        """

        if self._core is None:
            return knowledge_structure

        return self._core.evolve(
            knowledge_structure,
            operation,
        )

    #
    # Serialization
    #

    def serialize(
        self,
        knowledge_structure: Any,
    ) -> Any:
        """
        Delegate serialization to CKS Core.
        """

        if self._core is None:
            return knowledge_structure

        return self._core.serialize(
            knowledge_structure,
        )

    #
    # Explainability
    #

    def explain(
        self,
        knowledge_structure: Any,
    ) -> Any:
        """
        Delegate semantic explanation to CKS Core.
        """

        if self._core is None:
            return None

        return self._core.explain(
            knowledge_structure,
        )