"""
Runtime configuration.

SPEC-001 Runtime Overview.

Contains Runtime-wide configuration that controls
operational behaviour.

Configuration never owns Runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version


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
        return "0.6.2"


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