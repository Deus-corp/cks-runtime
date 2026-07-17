"""
Runtime ↔ Core boundary.

Public API for communication between
CKS Runtime and semantic Core plugins.

Runtime depends only on these abstractions.
"""

from .bridge import CoreBridge
from .interfaces import CoreInterface
from .validation_result import (
    RuntimeValidationResult,
)


__all__ = [
    "CoreBridge",
    "CoreInterface",
    "RuntimeValidationResult",
]