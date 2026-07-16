import pytest

from cks_runtime import Runtime

from cks_runtime.core_api.interfaces import (
    CoreInterface,
)
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.pipeline.execution_pipeline import (
    ExecutionPipeline,
)
from cks_runtime.versioning.version import (
    RuntimeVersion,
)


class ValidCore(CoreInterface):

    def validate(
        self,
        knowledge_structure,
    ):
        return RuntimeValidationResult(
            valid=True,
        )

    def serialize(
        self,
        knowledge_structure,
    ):
        return knowledge_structure

    def evolve(
        self,
        knowledge_structure,
        operation,
    ):
        return knowledge_structure

    def explain(
        self,
        knowledge_structure,
    ):
        return {}


class InvalidCore(CoreInterface):

    def validate(
        self,
        knowledge_structure,
    ):
        return RuntimeValidationResult(
            valid=False,
        )

    def serialize(
        self,
        knowledge_structure,
    ):
        return knowledge_structure

    def evolve(
        self,
        knowledge_structure,
        operation,
    ):
        return knowledge_structure

    def explain(
        self,
        knowledge_structure,
    ):
        return {}


def create_runtime(
    core=None,
):
    return Runtime(
        core=core,
    )

def test_pipeline_commit_with_valid_core():

    runtime = create_runtime(
        ValidCore(),
    )

    session = runtime.create_session({})

    transaction = runtime.begin_transaction(
        session,
    )

    pipeline = ExecutionPipeline(
        runtime,
    )

    version = pipeline.commit(
        transaction,
    )

    assert isinstance(
        version,
        RuntimeVersion,
    )

    assert transaction.completed