"""
Runtime-native Merge Conflict model.

Core implementations may raise their own, implementation-specific
merge-conflict exceptions (e.g. cks-core's ``MergeConflictError``).
Runtime never propagates those upward as-is -- the same boundary rule
already applied to diagnostics in ``cks_runtime_plugins.cks_core``'s
``_translate_diagnostic`` applies here: only this Runtime-native shape
crosses the CoreInterface/CoreBridge boundary, so callers never need
to know which Core implementation is attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeMergeConflict:
    """
    One identity that two merged branches both changed, relative to a
    common base, to different, irreconcilable results.

    ``base``, ``branch_a``, and ``branch_b`` hold whatever
    Core-native representation of the object each structure had --
    ``None`` means the identity was absent there. Runtime treats these
    as opaque (``Any``), the same way it treats a Knowledge Structure
    itself: it never interprets Core-native content.
    """

    object_id: str
    base: Any | None
    branch_a: Any | None
    branch_b: Any | None


class RuntimeMergeConflictError(ValueError):
    """
    Raised by ``CoreBridge.merge()`` when the two branches changed one
    or more of the same identities to different, irreconcilable
    results.

    ``error.conflicts`` lists every such identity so the caller (e.g.
    a MergeOperation, or an MCP tool surfacing this to an LLM agent)
    can present or resolve each one.
    """

    def __init__(self, conflicts: list[RuntimeMergeConflict]) -> None:
        self.conflicts = conflicts
        ids = ", ".join(c.object_id for c in conflicts)
        super().__init__(f"Merge conflict on identities: {ids}")
