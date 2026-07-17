"""
Canonical Runtime Operations.

Concrete Runtime Operations.

Operations describe *what* should be executed.

Execution itself belongs to OperationExecutor.

Semantic behaviour always belongs to Runtime Plugins
(via CoreBridge).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cks_runtime.execution.operation_executor import (
    Operation,
)


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


@dataclass(slots=True)
class ValidateOperation(Operation):
    """
    Validate a Knowledge Structure.
    """

    operation_type: str = "validate"

    knowledge_structure: Any = None


# ---------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------


@dataclass(slots=True)
class EvolveOperation(Operation):
    """
    Apply a semantic evolution.
    """

    operation_type: str = "evolve"

    knowledge_structure: Any = None

    evolution: Any = None


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------


@dataclass(slots=True)
class SerializeOperation(Operation):
    """
    Serialize a Knowledge Structure.
    """

    operation_type: str = "serialize"

    knowledge_structure: Any = None


# ---------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------


@dataclass(slots=True)
class ExplainOperation(Operation):
    """
    Produce a semantic explanation.
    """

    operation_type: str = "explain"

    knowledge_structure: Any = None