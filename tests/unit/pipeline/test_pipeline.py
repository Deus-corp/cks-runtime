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
from cks_runtime.operations.operation_types import EvolveOperation


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


class EvolvingCore(CoreInterface):
    """Fake Core that returns a distinguishable structure from evolve()."""
    def validate(self, knowledge_structure):
        return RuntimeValidationResult(valid=True)
    def serialize(self, knowledge_structure):
        return knowledge_structure
    def evolve(self, knowledge_structure, operation):
        return {"evolved": True, "original": knowledge_structure}
    def explain(self, knowledge_structure):
        return {}

def test_commit_persists_evolve_result_into_version():
    runtime = create_runtime(EvolvingCore())
    original_structure = {"objects": ["obj-1"]}
    session = runtime.create_session(original_structure)
    transaction = runtime.begin_transaction(session)
    transaction.add_operation(
        EvolveOperation("evolve", knowledge_structure=original_structure, evolution=[])
    )
    version = runtime.commit_transaction(transaction)
    assert version.knowledge_structure != original_structure
    assert version.knowledge_structure["evolved"] is True
    assert session.knowledge_structure["evolved"] is True

def test_commit_does_not_mutate_session_for_readonly_operations():
    from cks_runtime.operations.operation_types import ValidateOperation
    runtime = create_runtime(EvolvingCore())
    original_structure = {"objects": ["obj-1"]}
    session = runtime.create_session(original_structure)
    transaction = runtime.begin_transaction(session)
    transaction.add_operation(
        ValidateOperation("validate", knowledge_structure=original_structure)
    )
    runtime.commit_transaction(transaction)
    assert session.knowledge_structure == original_structure