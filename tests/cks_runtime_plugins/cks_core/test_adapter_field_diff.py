"""
Unit tests for CksCoreAdapter.field_diff().

Unlike most of test_adapter.py, these use real cks.parse()-built
KnowledgeStructures rather than mocks: field_diff() walks the actual
object model (.objects, .get(), CanonicalRelation, .structure), so a
mock would just be re-asserting the mock's own return value.
"""

from __future__ import annotations

import cks

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime_plugins.cks_core.adapter import CksCoreAdapter


def _structure(objects_json: str) -> cks.core.KnowledgeStructure:
    return cks.parse(f'{{"objects": [{objects_json}]}}')


ADAPTER = CksCoreAdapter()


def test_no_changes_reports_nothing():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, "structure": {"a": 1}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, "structure": {"a": 1}}'
    )

    assert ADAPTER.field_diff(source, target) == []


def test_added_object_reports_add_object():
    source = _structure("")
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, "structure": {}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(object_id="obj-1", op_type="add_object")
    ]


def test_removed_object_reports_remove_object():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, "structure": {}}'
    )
    target = _structure("")

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(object_id="obj-1", op_type="remove_object")
    ]


def test_changed_field_reports_set_field_only_for_the_changed_key():
    """
    The crux of ADR-007: a change to one field of an object must be
    reported at field granularity, and an untouched sibling field
    (`size` here) must NOT show up as changed.
    """
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, '
        '"structure": {"color": "red", "size": 1}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, '
        '"structure": {"color": "blue", "size": 1}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(
            object_id="obj-1", op_type="set_field", field_key="color", field_value="blue"
        )
    ]


def test_removed_field_reports_delete_field():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, '
        '"structure": {"color": "red"}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, "structure": {}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(
            object_id="obj-1", op_type="delete_field", field_key="color"
        )
    ]


def test_multiple_changed_fields_are_all_reported_sorted_by_key():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, '
        '"structure": {"color": "red", "size": 1}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n"}, '
        '"structure": {"color": "blue", "size": 2}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(
            object_id="obj-1", op_type="set_field", field_key="color", field_value="blue"
        ),
        RuntimeFieldOperation(
            object_id="obj-1", op_type="set_field", field_key="size", field_value=2
        ),
    ]


def test_added_relation_reports_add_relation():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}},'
        '{"identity": {"id": "rel-1", "type": "Relation", "name": "r"}, '
        '"structure": {"participants": ["obj-1", "obj-2"], "relation_type": "relates_to"}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(object_id="rel-1", op_type="add_relation")
    ]


def test_removed_relation_reports_remove_relation():
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}},'
        '{"identity": {"id": "rel-1", "type": "Relation", "name": "r"}, '
        '"structure": {"participants": ["obj-1", "obj-2"], "relation_type": "relates_to"}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(object_id="rel-1", op_type="remove_relation")
    ]


def test_changed_relation_reports_remove_and_add_not_set_field():
    """
    Relations have no granular update in cks-core (UpdateObject
    explicitly rejects a CanonicalRelation target), so unlike a
    changed KnowledgeObject, a changed relation's content must be
    reported as a remove+add pair, never set_field.
    """
    source = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}},'
        '{"identity": {"id": "rel-1", "type": "Relation", "name": "r"}, '
        '"structure": {"participants": ["obj-1", "obj-2"], "relation_type": "relates_to", '
        '"weight": 1}}'
    )
    target = _structure(
        '{"identity": {"id": "obj-1", "type": "T", "name": "n1"}, "structure": {}},'
        '{"identity": {"id": "obj-2", "type": "T", "name": "n2"}, "structure": {}},'
        '{"identity": {"id": "rel-1", "type": "Relation", "name": "r"}, '
        '"structure": {"participants": ["obj-1", "obj-2"], "relation_type": "relates_to", '
        '"weight": 2}}'
    )

    assert ADAPTER.field_diff(source, target) == [
        RuntimeFieldOperation(object_id="rel-1", op_type="remove_relation"),
        RuntimeFieldOperation(object_id="rel-1", op_type="add_relation"),
    ]