"""
Tests for DiffOperation.
"""

import cks
import pytest
from cks.evolution import AddObject, compose

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import DiffOperation, EvolveOperation
from cks_runtime.runtime import Runtime

pytestmark = pytest.mark.asyncio


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


async def _evolve(runtime: Runtime, session, new_id: str):
    obj = cks.KnowledgeObject(cks.ObjectIdentity(id=new_id, type="Thing", name=new_id))
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=[AddObject(obj)],
        )
    )
    return await runtime.commit_transaction(tx)


async def test_diff_operation_against_target_structure():
    runtime = await Runtime.create(core=CksCoreAdapter())
    session = await runtime.create_session(make_structure(["a"]))

    target = make_structure(["a", "b"])

    tx = runtime.begin_transaction(session)
    tx.add_operation(DiffOperation("diff", target_structure=target))
    await runtime.commit_transaction(tx)

    assert len(tx.results) == 1
    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    reconstructed = compose(session.knowledge_structure, result.payload)
    assert reconstructed.root_hash == target.root_hash


async def test_diff_operation_requires_a_target():
    runtime = await Runtime.create(core=CksCoreAdapter())
    session = await runtime.create_session(make_structure(["a"]))

    tx = runtime.begin_transaction(session)
    tx.add_operation(DiffOperation("diff"))

    with pytest.raises(RuntimeError, match="failed"):
        await runtime.commit_transaction(tx)
