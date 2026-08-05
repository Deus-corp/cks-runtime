"""
CKS Runtime.

Canonical Runtime for the Canonical Knowledge Structure ecosystem.

The public package API intentionally exposes only the
high-level Runtime façade and its configuration object.
"""

from __future__ import annotations

from .config import RuntimeConfig
from .gc.garbage_collector import GarbageCollector
from .reasoning.contradiction_sweeper import ContradictionSweeper
from .reasoning.graph_auto_update_sweeper import GraphAutoUpdateSweeper
from .reasoning.inference_staleness_sweeper import InferenceStalenessSweeper
from .reasoning.temporal_staleness_sweeper import TemporalStalenessSweeper
from .runtime import Runtime

__version__ = RuntimeConfig().runtime_version

__all__ = (
    "ContradictionSweeper",
    "GarbageCollector",
    "GraphAutoUpdateSweeper",
    "InferenceStalenessSweeper",
    "Runtime",
    "RuntimeConfig",
    "TemporalStalenessSweeper",
    "__version__",
)