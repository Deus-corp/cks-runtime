"""
CKS Runtime -- concrete CoreInterface adapters.

Each module in this package implements ``cks_runtime.core_api.interfaces.CoreInterface``
against a specific canonical-knowledge library. ``cks_core`` (backed by the
required ``cks-core`` dependency) is the default and currently only adapter;
additional adapters may be added here in the future.
"""

from __future__ import annotations
