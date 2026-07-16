"""
Core boundary interfaces.

This module defines the exclusive semantic boundary
between CKS Runtime and CKS Core.

Runtime depends only on these abstractions.

Concrete Core implementations are supplied externally.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any


class CoreInterface(ABC):
    """
    Semantic boundary between Runtime and CKS Core.

    Runtime owns operational behaviour.

    CKS Core owns semantic behaviour.

    Runtime shall never implement semantic rules itself.
    """

    @abstractmethod
    def validate(
        self,
        knowledge_structure: Any,
    ) -> Any:
        """
        Execute canonical Core validation.

        Validation semantics belong exclusively
        to CKS Core.
        """

    @abstractmethod
    def serialize(
        self,
        knowledge_structure: Any,
    ) -> Any:
        """
        Produce canonical serialization.

        Serialization semantics belong exclusively
        to CKS Core.
        """

    @abstractmethod
    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Execute canonical semantic evolution.

        Runtime coordinates execution.

        CKS Core defines the resulting semantic state.

        Implementations should return the resulting
        Knowledge Structure.

        Runtime makes no assumptions about whether
        evolution is performed in-place or by returning
        a new object.
        """

    @abstractmethod
    def explain(
        self,
        knowledge_structure: Any,
    ) -> Any:
        """
        Produce semantic explanation.

        Runtime may expose explanations but never
        generates semantic explanations itself.
        """