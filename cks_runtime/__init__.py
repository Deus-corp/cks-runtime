"""
CKS Runtime.

Canonical Runtime for the Canonical Knowledge Structure ecosystem.

The public package API intentionally exposes only the
high-level Runtime façade and its configuration object.
"""

from __future__ import annotations

from .config import RuntimeConfig
from .runtime import Runtime

__version__ = RuntimeConfig().runtime_version

__all__ = (
    "Runtime",
    "RuntimeConfig",
    "__version__",
)