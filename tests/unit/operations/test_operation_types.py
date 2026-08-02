"""Basic tests for operation types existence."""

import pytest

from cks_runtime.operations.operation_types import (
    EvolveOperation,
    ExplainInferenceOperation,
    ExplainOperation,
    SerializeOperation,
    ValidateOperation,
)


def test_validate_operation_exists():
    assert ValidateOperation is not None

def test_evolve_operation_exists():
    assert EvolveOperation is not None

def test_serialize_operation_exists():
    assert SerializeOperation is not None

def test_explain_operation_exists():
    assert ExplainOperation is not None

def test_explain_inference_operation_exists():
    assert ExplainInferenceOperation is not None


@pytest.mark.asyncio
async def test_query_subgraph_operation_requires_seed_ids():
    from cks_runtime.execution.operation_executor import OperationStatus
    from cks_runtime.operations.operation_types import QuerySubgraphOperation

    op = QuerySubgraphOperation(seed_ids=None, knowledge_structure={})
    # execute вне рантайма можно протестировать, замокав executor.core
    class FakeCore:
        def query_subgraph(self, *args, **kwargs):
            return "fake_result"
    class FakeExecutor:
        core = FakeCore()
    result = await op.execute(session=None, executor=FakeExecutor())
    assert result.status == OperationStatus.FAILED
    assert "seed_ids" in str(result.error)


@pytest.mark.asyncio
async def test_explain_inference_operation_requires_object_id():
    from cks_runtime.execution.operation_executor import OperationStatus

    op = ExplainInferenceOperation(object_id=None, knowledge_structure={})

    class FakeCore:
        def explain_inference(self, *args, **kwargs):
            return "fake_result"
    class FakeExecutor:
        core = FakeCore()

    result = await op.execute(session=None, executor=FakeExecutor())
    assert result.status == OperationStatus.FAILED
    assert "object_id" in str(result.error)


@pytest.mark.asyncio
async def test_explain_inference_operation_delegates_to_core():
    from cks_runtime.execution.operation_executor import OperationStatus

    op = ExplainInferenceOperation(object_id="c1", knowledge_structure="ks")

    class FakeCore:
        def explain_inference(self, knowledge_structure, object_id):
            assert knowledge_structure == "ks"
            assert object_id == "c1"
            return {"object_id": "c1", "has_inference": True}
    class FakeExecutor:
        core = FakeCore()

    result = await op.execute(session=None, executor=FakeExecutor())
    assert result.status == OperationStatus.COMPLETED
    assert result.payload == {"object_id": "c1", "has_inference": True}


@pytest.mark.asyncio
async def test_explain_inference_operation_catches_unsupported_capability():
    """A Core that doesn't implement explain_inference (NotImplementedError,
    matching CoreInterface's optional-capability default) must surface as a
    FAILED ExecutionResult, not an unhandled exception -- same convention
    QuerySubgraphOperation's broad except already establishes."""
    from cks_runtime.execution.operation_executor import OperationStatus

    op = ExplainInferenceOperation(object_id="c1", knowledge_structure={})

    class FakeCore:
        def explain_inference(self, *args, **kwargs):
            raise NotImplementedError("FakeCore does not implement explain_inference()")
    class FakeExecutor:
        core = FakeCore()

    result = await op.execute(session=None, executor=FakeExecutor())
    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, NotImplementedError)