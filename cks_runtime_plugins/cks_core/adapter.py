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

from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.diagnostics.diagnostic import (
    Diagnostic as RuntimeDiagnostic,
    DiagnosticSeverity as RuntimeSeverity,
    DiagnosticSource,
)

_SEVERITY_MAP = {
    CoreSeverity.INFORMATION: RuntimeSeverity.INFO,
    CoreSeverity.WARNING: RuntimeSeverity.WARNING,
    CoreSeverity.ERROR: RuntimeSeverity.ERROR,
}


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

    def hash(self, knowledge_structure: Any) -> str:
        return knowledge_structure.root_hash