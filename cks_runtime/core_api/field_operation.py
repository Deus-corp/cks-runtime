"""
Runtime-native Field Operation.

A single field-level change to a KnowledgeObject. Produced by
``CoreInterface.field_diff()`` so Runtime's operation log (ADR-007)
has a stable shape to persist regardless of which Core implementation
produced the diff, and read back by ``RuntimeStorage.list_operations()``
with ``version_id`` populated to identify which committed Version each
entry belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    - ``"set_field"`` -- a scalar ``structure`` key is present in both
      ``source`` and ``target`` but its value changed (this includes
      a key appearing for the first time with any value, including a
      literal ``None``). ``field_key`` is the key name and
      ``field_value`` is the new (``target``) value.
    - ``"delete_field"`` -- a scalar ``structure`` key present in
      ``source`` is absent from ``target``. ``field_key`` is the key
      name; ``field_value`` is always ``None`` and must not be read
      as "the key's value is now None" -- the key is gone entirely.
      Kept distinct from ``"set_field"`` with ``field_value=None`` so
      the two are never confused when this operation is replayed
      (e.g. by ``CoreInterface.synthesize_merge()``).

    ``version_id`` identifies the committed ``RuntimeVersion`` this
    operation belongs to. It is ``None`` when an operation comes
    straight out of ``field_diff()`` (a diff isn't tied to a
    persisted version until ``ExecutionPipeline`` records it), and
    populated by ``RuntimeStorage.list_operations()`` when reading a
    previously-recorded operation back.
    """

    object_id: str
    op_type: str
    field_key: str | None = None
    field_value: Any = None
    version_id: str | None = None