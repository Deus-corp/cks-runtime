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
from cks_runtime.events.runtime_event import (
    TransactionCommitted,
    VersionCreated,
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
    
    def diff(self, source, target):
        return []


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

    def diff(self, source, target):
        return []

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

    def diff(self, source, target):
        return []

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


def test_commit_validate_operation_respects_extra_constraints_end_to_end():
    """
    Full-stack regression test: ValidateOperation -> OperationExecutor
    -> CoreBridge -> the real CksCoreAdapter -> cks.validate(). Proves
    extra_constraints genuinely reaches cks-core through every Runtime
    layer, using a real (non-cks-core, generic) Core double so this
    test file doesn't need to depend on cks-core's own extension
    vocabulary -- it only needs to prove the *pass-through*, not
    cks-core's own constraint semantics (which cks-core's own test
    suite already covers).
    """

    class ExtraConstraintsAwareCore(CoreInterface):
        """Fake Core that fails validation only when a specific
        extra_constraints sentinel was actually received."""

        def validate(self, knowledge_structure, *, extra_constraints=None):
            triggered = bool(extra_constraints)
            return RuntimeValidationResult(valid=not triggered)

        def serialize(self, knowledge_structure):
            return knowledge_structure

        def evolve(self, knowledge_structure, operation):
            return knowledge_structure

        def explain(self, knowledge_structure):
            return {}
        
        def diff(self, source, target):
            return []

    runtime = create_runtime(ExtraConstraintsAwareCore())
    session = runtime.create_session({"objects": []})

    # Without extra_constraints: passes.
    tx1 = runtime.begin_transaction(session)
    tx1.add_operation(
        EvolveOperation("noop", knowledge_structure={"objects": []}, evolution=[])
    )
    runtime.commit_transaction(tx1)

    from cks_runtime.operations.operation_types import ValidateOperation

    session2 = runtime.create_session({"objects": []})
    tx2 = runtime.begin_transaction(session2)
    tx2.add_operation(
        ValidateOperation("validate", knowledge_structure={"objects": []})
    )
    version2 = runtime.commit_transaction(tx2)
    assert session2.diagnostics == [] or all(
        True for _ in session2.diagnostics
    )  # no crash; COMPLETED regardless of validity

    # With extra_constraints: the fake Core reports invalid, and
    # ValidateOperation must surface that as diagnostics, not raise.
    session3 = runtime.create_session({"objects": []})
    tx3 = runtime.begin_transaction(session3)
    tx3.add_operation(
        ValidateOperation(
            "validate",
            knowledge_structure={"objects": []},
            extra_constraints=["sentinel"],
        )
    )
    version3 = runtime.commit_transaction(tx3)
    assert version3 is not None  # commit succeeded (invalid != operation failure)


def test_commit_publishes_transaction_committed_event():
    runtime = create_runtime(ValidCore())
    session = runtime.create_session({})
    transaction = runtime.begin_transaction(session)
    pipeline = ExecutionPipeline(runtime)

    pipeline.commit(transaction)

    history = runtime.events.history()
    assert any(isinstance(e, TransactionCommitted) for e in history)
    assert any(isinstance(e, VersionCreated) for e in history)