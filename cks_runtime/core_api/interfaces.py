"""
Runtime ↔ Core semantic contract.

Runtime depends exclusively on this interface.

Concrete semantic engines are supplied through
Runtime plugins.

This interface defines the complete semantic boundary
between Runtime and any Core implementation.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class CoreInterface(ABC):
    """
    Runtime semantic interface.

    Runtime owns:

        • execution
        • orchestration
        • transactions
        • sessions
        • versions

    Core implementations own:

        • semantic validation
        • semantic evolution
        • serialization
        • semantic explanation
    """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @abstractmethod
    def validate(
        self,
        knowledge_structure: Any,
        *,
        extra_constraints: Any = None,
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure.

        Parameters
        ----------
        extra_constraints
            Optional, Core-implementation-defined extra validation
            rules to apply for this call only (e.g. an opt-in
            extension constraint). A Core implementation that has no
            notion of scoped/extra constraints may ignore this
            parameter; Runtime never inspects or interprets it, it is
            passed through verbatim.

        Returns
        -------
        RuntimeValidationResult
            Canonical Runtime validation model.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    @abstractmethod
    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Apply one semantic evolution.

        Parameters
        ----------
        knowledge_structure
            Current Knowledge Structure.

        operation
            Canonical evolution description.

        Returns
        -------
        Any
            Resulting Knowledge Structure.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @abstractmethod
    def serialize(
        self,
        knowledge_structure: Any,
    ) -> str:
        """
        Produce canonical serialization.

        Returns
        -------
        str
            Canonical serialized representation.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Explainability
    # ------------------------------------------------------------------

    @abstractmethod
    def explain(
        self,
        knowledge_structure: Any,
    ) -> dict[str, Any]:
        """
        Produce a semantic explanation.

        Returns
        -------
        dict[str, Any]
            Structured semantic explanation.
        """
        raise NotImplementedError
    

    @abstractmethod
    def diff(self, source: Any, target: Any) -> list[Any]:
        """Compute structural diff between two Core-native structures."""
        raise NotImplementedError