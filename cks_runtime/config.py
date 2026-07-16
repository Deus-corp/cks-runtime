"""
Runtime configuration.

SPEC-001 Runtime Overview
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeConfig:
    """
    Runtime configuration.

    Future specifications may extend this configuration.
    """

    runtime_name: str = "CKS Runtime"

    runtime_version: str = "0.2.0"

    auto_version_on_commit: bool = True

    collect_runtime_diagnostics: bool = True