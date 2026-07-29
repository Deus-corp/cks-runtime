"""
Tests for MergeOperation.
"""

import cks
import pytest
from cks.evolution import AddObject, RemoveObject

from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import EvolveOperation, MergeOperation
from cks_runtime.runtime import Runtime
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


async def _evolve(runtime: Runtime, session, operations):
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=operations,
        )
    )
    await runtime.commit_transaction(tx)


def _add(obj_id: str) -> AddObject:
    return AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id=obj_id, type="Thing", name=obj_id))
    )


async def test_merge_operation_combines_non_conflicting_branches():
    """
    Branching, evolving each side independently, then merging the
    branch back into the session it forked from (using the branch's
    own recorded parent_version_id as the base) should combine both
    sides' additions.
    """
    runtime = await Runtime.create(core=CksCoreAdapter())

    trunk = await runtime.create_session(make_structure(["root"]))

    branch = await runtime.create_branch(trunk)
    assert branch.parent_session_id == trunk.session_id

    # Evolve trunk and branch independently.
    await _evolve(runtime, trunk, [_add("a")])
    await _evolve(runtime, branch, [_add("b")])

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(
        MergeOperation(
            "merge",
            source_session=branch,
            base_structure=make_structure(["root"]),
        )
    )
    await runtime.commit_transaction(tx)

    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    merged_ids = {obj.identity.id for obj in trunk.knowledge_structure.objects}
    assert merged_ids == {"root", "a", "b"}


async def test_merge_operation_uses_source_sessions_parent_version_id():
    """
    When no explicit base is given, the base is resolved from
    source_session.parent_version_id -- recorded automatically by
    Runtime.create_branch(session, version_id=...).
    """
    runtime = await Runtime.create(core=CksCoreAdapter())

    trunk = await runtime.create_session(make_structure(["root"]))
    await _evolve(runtime, trunk, [_add("a")])
    fork_version = runtime.latest_version(trunk)

    branch = await runtime.create_branch(trunk, version_id=fork_version.version_id)
    assert branch.parent_version_id == fork_version.version_id

    await _evolve(runtime, trunk, [_add("b")])
    await _evolve(runtime, branch, [_add("c")])

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(MergeOperation("merge", source_session=branch))
    await runtime.commit_transaction(tx)

    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    merged_ids = {obj.identity.id for obj in trunk.knowledge_structure.objects}
    assert merged_ids == {"root", "a", "b", "c"}


async def test_merge_operation_requires_source_session():
    runtime = await Runtime.create(core=CksCoreAdapter())
    session = await runtime.create_session(make_structure(["root"]))

    tx = runtime.begin_transaction(session)
    tx.add_operation(MergeOperation("merge"))

    with pytest.raises(RuntimeError, match="failed"):
        await runtime.commit_transaction(tx)


async def test_merge_operation_requires_a_resolvable_base():
    """
    A branch created without an explicit version_id has no
    parent_version_id to fall back on, so merging it without an
    explicit base fails with a clear error rather than guessing.
    """
    runtime = await Runtime.create(core=CksCoreAdapter())
    trunk = await runtime.create_session(make_structure(["root"]))
    branch = await runtime.create_branch(trunk)  # no version_id

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(MergeOperation("merge", source_session=branch))

    with pytest.raises(RuntimeError, match="failed"):
        await runtime.commit_transaction(tx)


async def test_merge_operation_surfaces_structured_conflicts_via_direct_execution():
    """
    Calling the operation directly through the executor (bypassing
    the transaction/commit path) preserves the structured
    RuntimeMergeConflictError with its .conflicts list -- this is the
    integration point an MCP-level merge_branch tool would use to
    surface conflicts to an LLM agent.
    """
    runtime = await Runtime.create(core=CksCoreAdapter())

    base = make_structure(["shared"])
    trunk = await runtime.create_session(base)
    branch = await runtime.create_branch(trunk)

    # Both sides modify the same identity differently -> hard conflict.
    trunk_edit = cks.KnowledgeObject(
        cks.ObjectIdentity(id="shared", type="Thing", name="shared"),
        structure={"note": "trunk edit"},
    )
    await _evolve(
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
    await runtime.commit_transaction(tx)

    operation = MergeOperation(
        "merge",
        source_session=branch,
        base_structure=base,
    )
    result = await runtime.executor.execute(operation, trunk)

    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, RuntimeMergeConflictError)
    conflict_ids = {c.object_id for c in result.error.conflicts}
    assert conflict_ids == {"shared"}

    # The failed direct execution never touched the session's state.
    assert {o.identity.id for o in trunk.knowledge_structure.objects} == {"shared"}


async def test_create_branch_without_version_id_defaults_to_latest_committed_version():
    """
    Regression test: create_branch(session) -- with no explicit
    version_id -- used to always leave parent_version_id unset, even
    when session already had a committed version identical to its
    current state (the common case: branching right after a
    validate/evolve/merge call). That forced merge_branch to fail with
    "could not determine a merge base" unless the caller remembered to
    pass version_id=... explicitly at branch time.

    Once session has at least one committed version and no operation
    is in flight, omitting version_id should still record that latest
    version as parent_version_id, since session.knowledge_structure is
    then provably identical to it.
    """
    runtime = await Runtime.create(core=CksCoreAdapter())

    trunk = await runtime.create_session(make_structure(["root"]))
    await _evolve(runtime, trunk, [_add("a")])
    latest = runtime.latest_version(trunk)

    branch = await runtime.create_branch(trunk)  # no version_id passed

    assert branch.parent_version_id == latest.version_id

    # And merge_branch can now resolve its base automatically, exactly
    # as if version_id had been passed explicitly at branch time.
    await _evolve(runtime, trunk, [_add("b")])
    await _evolve(runtime, branch, [_add("c")])

    tx = runtime.begin_transaction(trunk)
    tx.add_operation(MergeOperation("merge", source_session=branch))
    await runtime.commit_transaction(tx)

    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    merged_ids = {obj.identity.id for obj in trunk.knowledge_structure.objects}
    assert merged_ids == {"root", "a", "b", "c"}
