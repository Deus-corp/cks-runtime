"""
Shared, SSRF-safe outbound HTTP helpers for Runtime subsystems that
need to make real network requests (currently: GraphAutoUpdateSweeper).

See `cks_runtime.net.safe_fetch` for the implementation.
"""

from __future__ import annotations

from cks_runtime.net.safe_fetch import UnsafeURLError, safe_get

__all__ = ("UnsafeURLError", "safe_get")
