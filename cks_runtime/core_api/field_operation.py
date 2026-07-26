"""
Runtime-native Field Operation.

A single field-level change to a KnowledgeObject. Used exclusively by
``CoreInterface.field_diff()`` so Runtime's operation log (ADR-007)
has a stable shape to persist regardless of which Core implementation
produced the diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeFieldOperation:
    """
    A field-level operation on a KnowledgeObject.

    ``op_type`` is one of:

    - ``"add_object"`` / ``"add_relation"`` -- the identity first
      appears in ``target`` (``field_key`` and ``field_value`` are
      always ``None``).
    - ``"remove_object"`` / ``"remove_relation"`` -- the identity
      disappears in ``target`` (``field_key`` and ``field_value`` are
      always ``None``).
    - ``"set_field"`` -- a scalar ``structure`` key changed its value
      from ``source`` to ``target``. ``field_key`` is the key name
      and ``field_value`` is the new value (or ``None`` if the key
      was deleted).
    """

    object_id: str
    op_type: str
    field_key: str | None = None
    field_value: Any = None