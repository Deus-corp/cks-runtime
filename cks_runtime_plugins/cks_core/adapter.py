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
from cks.evolution import compose
from cks.interface import inspect as cks_inspect

from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
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
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure using CKS Core.
        """

        result = cks.validate(
            knowledge_structure,
        )

        return RuntimeValidationResult(
            valid=result.is_valid,
            diagnostics=tuple(
                result.diagnostics,
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