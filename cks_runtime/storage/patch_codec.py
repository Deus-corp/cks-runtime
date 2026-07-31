"""
Shared codec for delta (non-snapshot) RuntimeVersion patches.

A ``RuntimeVersion`` stores either a full ``knowledge_structure``
snapshot or a ``patch`` -- a list of ``cks.evolution.StructuralOperator``
instances describing the change since the previous version. Every
storage backend that persists versions as JSON needs to turn that list
into plain JSON-serializable dicts and back.

This lives in its own module (rather than duplicated per backend) so
that ``SQLiteStorage``, ``PostgresStorage``, and any future backend
stay behaviourally identical for exactly the same reason the storage
conformance suite exists: two independently-maintained copies of this
logic are two independent places for the set of supported operator
types to silently drift apart.
"""

from __future__ import annotations

from cks.core import CanonicalRelation, KnowledgeObject, ObjectIdentity
from cks.evolution import AddObject, AddRelation, RemoveObject, RemoveRelation


def serialize_operators(operators: list) -> list[dict]:
    """Convert a list of StructuralOperator instances to JSON-serializable dicts."""
    result = []
    for op in operators:
        if isinstance(op, AddObject):
            obj = op.obj
            result.append({
                "type": "add_object",
                "identity": {
                    "id": obj.identity.id,
                    "type": obj.identity.type,
                    "name": obj.identity.name,
                },
                "structure": dict(obj.structure),
            })
        elif isinstance(op, AddRelation):
            rel = op.relation
            result.append({
                "type": "add_relation",
                "identity": {
                    "id": rel.identity.id,
                    "type": rel.identity.type,
                    "name": rel.identity.name,
                },
                "participants": list(rel.participants),
                "relation_type": rel.relation_type,
                "structure": dict(rel.structure),
            })
        elif isinstance(op, RemoveObject):
            result.append({
                "type": "remove_object",
                "object_id": op.object_id,
            })
        elif isinstance(op, RemoveRelation):
            result.append({
                "type": "remove_relation",
                "relation_id": op.relation_id,
            })
        else:
            raise TypeError(f"Unknown operator type: {type(op)}")
    return result


def deserialize_operators(data: list[dict]) -> list:
    """Reconstruct StructuralOperators from JSON dicts."""
    operators = []
    for item in data:
        op_type = item["type"]
        if op_type == "add_object":
            identity = ObjectIdentity(**item["identity"])
            obj = KnowledgeObject(identity=identity, structure=item.get("structure", {}))
            operators.append(AddObject(obj))
        elif op_type == "add_relation":
            identity = ObjectIdentity(**item["identity"])
            rel = CanonicalRelation(
                identity=identity,
                participants=item["participants"],
                relation_type=item["relation_type"],
                structure=item.get("structure", {}),
            )
            operators.append(AddRelation(rel))
        elif op_type == "remove_object":
            operators.append(RemoveObject(item["object_id"]))
        elif op_type == "remove_relation":
            operators.append(RemoveRelation(item["relation_id"]))
        else:
            raise ValueError(f"Unknown operator type: {op_type}")
    return operators
