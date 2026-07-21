"""
Tests for MergeOperation.
"""

import cks
import pytest
from cks.evolution import AddObject, RemoveObject, compose
from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import EvolveOperation, MergeOperation
from cks_runtime.runtime import Runtime
from cks_runtime_plugins.cks_core import CksCoreAdapter


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def _evolve(runtime: Runtime, session, operations):
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=operations,
        )
    )
    runtime.commit_transaction(tx)


def _add(obj_id: str) -> AddObject:
    return AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id=obj_id, type="Thing", name=obj_id))
    )


def test_merge_operation_combines_non_conflicting_branches():
    """
    Branching, evolving each side independently, then merging the
    branch back into the session it forked from (using the branch's
    own recorded parent_version_id as the base) should combine both
    sides' additions.
    """
    runtime = Runtime(core=CksCoreAdapter())

    trunk = runtime.create_session(make_structure(["root"]))
    fork_point = runtime.latest_version(trunk)  # None: nothing committed yet

    branch = runtime.create_branch(trunk)
    assert branch.parent_session_id == trunk.session_id

    # Evolve trunk and branch independently.
    _evolve(runtime, trunk, [_add("a")])
    _evolve(runtime, branch, [_add("b")])

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(
        MergeOperation(
            "merge",
            source_session=branch,
            base_structure=make_structure(["root"]),
        )
    )
    runtime.commit_transaction(tx)

    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    merged_ids = {obj.identity.id for obj in trunk.knowledge_structure.objects}
    assert merged_ids == {"root", "a", "b"}


def test_merge_operation_uses_source_sessions_parent_version_id():
    """
    When no explicit base is given, the base is resolved from
    source_session.parent_version_id -- recorded automatically by
    Runtime.create_branch(session, version_id=...).
    """
    runtime = Runtime(core=CksCoreAdapter())

    trunk = runtime.create_session(make_structure(["root"]))
    _evolve(runtime, trunk, [_add("a")])
    fork_version = runtime.latest_version(trunk)

    branch = runtime.create_branch(trunk, version_id=fork_version.version_id)
    assert branch.parent_version_id == fork_version.version_id

    _evolve(runtime, trunk, [_add("b")])
    _evolve(runtime, branch, [_add("c")])

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(MergeOperation("merge", source_session=branch))
    runtime.commit_transaction(tx)

    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    merged_ids = {obj.identity.id for obj in trunk.knowledge_structure.objects}
    assert merged_ids == {"root", "a", "b", "c"}


def test_merge_operation_requires_source_session():
    runtime = Runtime(core=CksCoreAdapter())
    session = runtime.create_session(make_structure(["root"]))

    tx = runtime.begin_transaction(session)
    tx.add_operation(MergeOperation("merge"))

    with pytest.raises(RuntimeError, match="failed"):
        runtime.commit_transaction(tx)


def test_merge_operation_requires_a_resolvable_base():
    """
    A branch created without an explicit version_id has no
    parent_version_id to fall back on, so merging it without an
    explicit base fails with a clear error rather than guessing.
    """
    runtime = Runtime(core=CksCoreAdapter())
    trunk = runtime.create_session(make_structure(["root"]))
    branch = runtime.create_branch(trunk)  # no version_id

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(MergeOperation("merge", source_session=branch))

    with pytest.raises(RuntimeError, match="failed"):
        runtime.commit_transaction(tx)


def test_merge_operation_surfaces_structured_conflicts_via_direct_execution():
    """
    Calling the operation directly through the executor (bypassing
    the transaction/commit path) preserves the structured
    RuntimeMergeConflictError with its .conflicts list -- this is the
    integration point an MCP-level merge_branch tool would use to
    surface conflicts to an LLM agent.
    """
    runtime = Runtime(core=CksCoreAdapter())

    base = make_structure(["shared"])
    trunk = runtime.create_session(base)
    branch = runtime.create_branch(trunk)

    # Both sides modify the same identity differently -> hard conflict.
    trunk_edit = cks.KnowledgeObject(
        cks.ObjectIdentity(id="shared", type="Thing", name="shared"),
        structure={"note": "trunk edit"},
    )
    _evolve(
        runtime,
        trunk,
        [RemoveObject("shared"), AddObject(trunk_edit)],
    )
    conflicting_obj = cks.KnowledgeObject(
        cks.ObjectIdentity(id="shared", type="Thing", name="renamed"),
        structure={"note": "branch edit"},
    )
    tx = runtime.begin_transaction(branch)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=branch.knowledge_structure,
            evolution=[RemoveObject("shared"), AddObject(conflicting_obj)],
        )
    )
    runtime.commit_transaction(tx)

    operation = MergeOperation(
        "merge",
        source_session=branch,
        base_structure=base,
    )
    result = runtime.executor.execute(operation, trunk)

    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, RuntimeMergeConflictError)
    conflict_ids = {c.object_id for c in result.error.conflicts}
    assert conflict_ids == {"shared"}

    # The failed direct execution never touched the session's state.
    assert {o.identity.id for o in trunk.knowledge_structure.objects} == {"shared"}
