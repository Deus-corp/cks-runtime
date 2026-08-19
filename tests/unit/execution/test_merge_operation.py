"""
Tests for MergeOperation.
"""

import cks
import pytest
from cks.evolution import AddObject, RemoveObject

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import EvolveOperation, MergeOperation
from cks_runtime.runtime import Runtime

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


async def test_merge_operation_resolves_empty_state_with_no_history_entry():
    """
    EMPTY_STATE_VERSION_ID resolves without needing to exist in
    either session's version_history at all -- unlike an ordinary
    base_version_id, which must be a real, resolvable committed
    version (see get_version_state()). This is what lets two
    sessions from *different* Runtimes/storage backends -- which
    have never shared any version -- still name a common ancestor.
    """
    from cks_runtime.operations.operation_types import EMPTY_STATE_VERSION_ID

    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure([]))
    session_a.parent_version_id = EMPTY_STATE_VERSION_ID
    await _evolve(runtime_a, session_a, [_add("from-a")])

    session_b = await runtime_b.create_session(make_structure([]))
    session_b.parent_version_id = EMPTY_STATE_VERSION_ID
    await _evolve(runtime_b, session_b, [_add("from-b")])

    # Neither session ever appears in the other's version_history --
    # confirming the short-circuit truly needs no shared storage.
    assert EMPTY_STATE_VERSION_ID not in [
        v.version_id for v in session_a.version_history
    ]

    operation = MergeOperation("merge", source_session=session_b)
    result = await runtime_a.executor.execute(operation, session_a)

    assert result.status == OperationStatus.COMPLETED
    merged_ids = {obj.identity.id for obj in result.payload.objects}
    assert merged_ids == {"from-a", "from-b"}


async def test_merge_operation_empty_state_converges_identical_concurrent_additions():
    """Same object, same id, added independently on both sides: not a conflict."""
    from cks_runtime.operations.operation_types import EMPTY_STATE_VERSION_ID

    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure([]))
    session_a.parent_version_id = EMPTY_STATE_VERSION_ID
    await _evolve(runtime_a, session_a, [_add("root")])

    session_b = await runtime_b.create_session(make_structure([]))
    session_b.parent_version_id = EMPTY_STATE_VERSION_ID
    await _evolve(runtime_b, session_b, [_add("root")])

    operation = MergeOperation("merge", source_session=session_b)
    result = await runtime_a.executor.execute(operation, session_a)

    assert result.status == OperationStatus.COMPLETED
    merged_ids = {obj.identity.id for obj in result.payload.objects}
    assert merged_ids == {"root"}


async def test_merge_operation_without_empty_state_anchor_still_fails():
    """
    Sanity check: EMPTY_STATE_VERSION_ID is opt-in. Two sessions that
    were never anchored to it (parent_version_id left at its None
    default) get the original, unresolvable-base failure -- this
    isn't a global behaviour change for ordinary ADR-007 usage.
    """
    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure([]))
    await _evolve(runtime_a, session_a, [_add("from-a")])

    session_b = await runtime_b.create_session(make_structure([]))
    await _evolve(runtime_b, session_b, [_add("from-b")])

    operation = MergeOperation("merge", source_session=session_b)
    result = await runtime_a.executor.execute(operation, session_a)

    assert result.status == OperationStatus.FAILED
    assert "could not determine a merge base" in str(result.error)