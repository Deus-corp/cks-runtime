"""
Tests for DiffOperation.
"""

import cks
import pytest
from cks.evolution import AddObject, compose
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import DiffOperation, EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime_plugins.cks_core import CksCoreAdapter


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def _evolve(runtime: Runtime, session, new_id: str):
    obj = cks.KnowledgeObject(cks.ObjectIdentity(id=new_id, type="Thing", name=new_id))
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=[AddObject(obj)],
        )
    )
    return runtime.commit_transaction(tx)


def test_diff_operation_against_target_structure():
    runtime = Runtime(core=CksCoreAdapter())
    session = runtime.create_session(make_structure(["a"]))

    target = make_structure(["a", "b"])

    tx = runtime.begin_transaction(session)
    tx.add_operation(DiffOperation("diff", target_structure=target))
    runtime.commit_transaction(tx)

    assert len(tx.results) == 1
    result = tx.results[0]
    assert result.status == OperationStatus.COMPLETED

    reconstructed = compose(session.knowledge_structure, result.payload)
    assert reconstructed.root_hash == target.root_hash


def test_diff_operation_requires_a_target():
    runtime = Runtime(core=CksCoreAdapter())
    session = runtime.create_session(make_structure(["a"]))

    tx = runtime.begin_transaction(session)
    tx.add_operation(DiffOperation("diff"))

    with pytest.raises(RuntimeError, match="failed"):
        runtime.commit_transaction(tx)