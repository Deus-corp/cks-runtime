"""
CKS Runtime – CKS Core Adapter.

Provides the concrete implementation of the CoreInterface
using the real `cks-core` library.

This adapter translates between the abstract Runtime model
and the actual CKS Core Python API.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import RuntimeValidationResult

import cks


class CksCoreAdapter(CoreInterface):
    """
    Concrete adapter that wraps the canonical `cks-core` library.

    All semantic operations are delegated to `cks-core`.
    The adapter is responsible for translating core-specific
    objects (e.g., ValidationResult) into the Runtime model
    (RuntimeValidationResult).
    """

    def validate(self, knowledge_structure: Any) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure using cks-core.

        Converts the native cks ValidationResult into a
        RuntimeValidationResult.
        """
        result = cks.validate(knowledge_structure)
        return RuntimeValidationResult(
            valid=result.is_valid,
            diagnostics=tuple(result.diagnostics),
            metadata=dict(result.metadata),
        )

    def serialize(self, knowledge_structure: Any) -> str:
        """Serialize a Knowledge Structure to canonical JSON."""
        return cks.serialize(knowledge_structure)

    def evolve(self, knowledge_structure: Any, operation: Any) -> Any:
        """
        Apply a structural evolution through cks-core.

        The operation is expected to be a list of operation
        dictionaries compatible with cks.evolution.compose.
        """
        from cks.evolution import compose

        if not isinstance(operation, (list, tuple)):
            raise ValueError("Evolution operation must be a sequence of operators.")

        return compose(knowledge_structure, operation)

    def explain(self, knowledge_structure: Any) -> dict:
        """
        Produce a semantic explanation using cks-core introspection.

        The current implementation returns a structured summary.
        Future versions may integrate a more sophisticated
        explainability engine.
        """
        from cks.interface import inspect as cks_inspect

        summary = cks_inspect(knowledge_structure)
        return {
            "object_count": len(knowledge_structure.objects),
            "relation_count": len(knowledge_structure.relations()),
            "summary": summary,
        }