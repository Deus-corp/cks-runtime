"""Basic tests for operation types existence."""

from cks_runtime.operations.operation_types import (
    ExplainOperation,
    EvolveOperation,
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


def test_query_subgraph_operation_requires_seed_ids():
    from cks_runtime.operations.operation_types import QuerySubgraphOperation
    from cks_runtime.execution.operation_executor import OperationStatus

    op = QuerySubgraphOperation(seed_ids=None, knowledge_structure={})
    # execute вне рантайма можно протестировать, замокав executor.core
    class FakeCore:
        def query_subgraph(self, *args, **kwargs):
            return "fake_result"
    class FakeExecutor:
        core = FakeCore()
    result = op.execute(session=None, executor=FakeExecutor())
    assert result.status == OperationStatus.FAILED
    assert "seed_ids" in str(result.error)