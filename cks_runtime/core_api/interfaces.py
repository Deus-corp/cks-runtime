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
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure.

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