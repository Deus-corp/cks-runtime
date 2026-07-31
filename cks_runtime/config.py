"""
Runtime configuration.

SPEC-001 Runtime Overview.

Contains Runtime-wide configuration that controls
operational behaviour.

Configuration never owns Runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version


def _runtime_version() -> str:
    """
    Return the installed Runtime version.

    Falls back to a development version when the
    package metadata is unavailable (for example,
    during local development before installation).
    """

    try:
        return version("cks-runtime")
    except PackageNotFoundError:
        return "1.25.0"


@dataclass(slots=True)
class RuntimeConfig:
    """
    Runtime configuration.

    This object contains only Runtime-wide options.

    Future Runtime specifications may extend it with
    persistence, execution and telemetry settings.
    """

    runtime_name: str = "CKS Runtime"
    runtime_version: str = _runtime_version()
    auto_version_on_commit: bool = True
    collect_runtime_diagnostics: bool = True
    storage_path: str = ":memory:"
    # Session garbage collection.
    # Set gc_retention=None to disable GC entirely.
    gc_retention: timedelta | None = field(default_factory=lambda: timedelta(hours=24))
    gc_sweep_interval: float = 600.0   # seconds between sweeps (default 10 min)
    gc_batch_size: int = 100           # max sessions evicted per sweep