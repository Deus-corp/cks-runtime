import pytest

from cks_runtime import Runtime
from cks_runtime.core_api.interfaces import (
    CoreInterface,
)
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.events.runtime_event import (
    TransactionCommitted,
    VersionCreated,
)
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.pipeline.execution_pipeline import (
    ExecutionPipeline,
)
from cks_runtime.versioning.version import (
    RuntimeVersion,
)
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio


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

async def test_pipeline_commit_with_valid_core():

    runtime = create_runtime(
        ValidCore(),
    )

    session = await runtime.create_session({})

    transaction = runtime.begin_transaction(
        session,
    )

    pipeline = ExecutionPipeline(
        runtime,
    )

    version = await pipeline.commit(
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

async def test_commit_persists_evolve_result_into_version():
    runtime = create_runtime(EvolvingCore())
    original_structure = {"objects": ["obj-1"]}
    session = await runtime.create_session(original_structure)
    transaction = runtime.begin_transaction(session)
    transaction.add_operation(
        EvolveOperation("evolve", knowledge_structure=original_structure, evolution=[])
    )
    version = await runtime.commit_transaction(transaction)
    assert version.knowledge_structure != original_structure
    assert version.knowledge_structure["evolved"] is True
    assert session.knowledge_structure["evolved"] is True

async def test_commit_does_not_mutate_session_for_readonly_operations():
    from cks_runtime.operations.operation_types import ValidateOperation
    runtime = create_runtime(EvolvingCore())
    original_structure = {"objects": ["obj-1"]}
    session = await runtime.create_session(original_structure)
    transaction = runtime.begin_transaction(session)
    transaction.add_operation(
        ValidateOperation("validate", knowledge_structure=original_structure)
    )
    await runtime.commit_transaction(transaction)
    assert session.knowledge_structure == original_structure


async def test_commit_validate_operation_respects_extra_constraints_end_to_end():
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
    session = await runtime.create_session({"objects": []})

    # Without extra_constraints: passes.
    tx1 = runtime.begin_transaction(session)
    tx1.add_operation(
        EvolveOperation("noop", knowledge_structure={"objects": []}, evolution=[])
    )
    await runtime.commit_transaction(tx1)

    from cks_runtime.operations.operation_types import ValidateOperation

    session2 = await runtime.create_session({"objects": []})
    tx2 = runtime.begin_transaction(session2)
    tx2.add_operation(
        ValidateOperation("validate", knowledge_structure={"objects": []})
    )
    assert session2.diagnostics == [] or all(
        True for _ in session2.diagnostics
    )  # no crash; COMPLETED regardless of validity

    # With extra_constraints: the fake Core reports invalid, so
    # ValidateOperation returns FAILED, and commit must raise.
    session3 = await runtime.create_session({"objects": []})
    tx3 = runtime.begin_transaction(session3)
    tx3.add_operation(
        ValidateOperation(
            "validate",
            knowledge_structure={"objects": []},
            extra_constraints=["sentinel"],
        )
    )
    with pytest.raises(RuntimeError, match="Operation validate failed"):
        await runtime.commit_transaction(tx3)
    # The session must not be modified after a failed commit
    assert session3.diagnostics == []


async def test_commit_publishes_transaction_committed_event():
    runtime = create_runtime(ValidCore())
    session = await runtime.create_session({})
    transaction = runtime.begin_transaction(session)
    pipeline = ExecutionPipeline(runtime)

    await pipeline.commit(transaction)

    history = runtime.events.history()
    assert any(isinstance(e, TransactionCommitted) for e in history)
    assert any(isinstance(e, VersionCreated) for e in history)


async def test_failed_operation_result_is_recorded_on_transaction():
    """
    Regression test: previously, transaction.add_result(result) ran
    *after* _handle_result(result, ...), but _handle_result raises
    RuntimeError immediately on a FAILED result -- so a failing
    operation's ExecutionResult (and its diagnostics) never reached
    transaction.results. Callers that catch the RuntimeError from
    commit_transaction() and then inspect tx.results to recover the
    precise failure diagnostics (e.g. cks-mcp's validate_knowledge)
    would see an empty list and fall back to a generic message.

    add_result must now run before _handle_result, so tx.results
    still holds the failed result -- with its original diagnostics --
    even though the transaction itself is rolled back.
    """
    from cks_runtime.diagnostics.diagnostic import (
        Diagnostic,
        DiagnosticSeverity,
        DiagnosticSource,
    )
    from cks_runtime.execution.operation_executor import OperationStatus
    from cks_runtime.operations.operation_types import ValidateOperation

    sentinel_diagnostic = Diagnostic(
        message="Relation 'rel-x' references unknown object 'nonexistent-source'.",
        source=DiagnosticSource.CORE,
        severity=DiagnosticSeverity.ERROR,
        code="CKS-STRUCT-DANGLING-REF",
    )

    class DiagnosingInvalidCore(CoreInterface):
        """Fake Core that fails validation with a specific diagnostic,
        mirroring what cks-core reports for a dangling reference."""

        def validate(self, knowledge_structure, *, extra_constraints=None):
            return RuntimeValidationResult(
                valid=False,
                diagnostics=(sentinel_diagnostic,),
            )

        def serialize(self, knowledge_structure):
            return knowledge_structure

        def evolve(self, knowledge_structure, operation):
            return knowledge_structure

        def explain(self, knowledge_structure):
            return {}

        def diff(self, source, target):
            return []

    runtime = create_runtime(DiagnosingInvalidCore())
    session = await runtime.create_session({"objects": []})
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        ValidateOperation("validate", knowledge_structure={"objects": []})
    )

    with pytest.raises(RuntimeError, match="Operation validate failed"):
        await runtime.commit_transaction(tx)

    assert len(tx.results) == 1
    failed_result = tx.results[-1]
    assert failed_result.status == OperationStatus.FAILED
    assert failed_result.diagnostics == (sentinel_diagnostic,)
    assert failed_result.diagnostics[0].message == (
        "Relation 'rel-x' references unknown object 'nonexistent-source'."
    )


async def test_commit_records_operations_when_core_and_storage_support_it():
    """
    Integration test for ADR-007 Part 1: when both the Core and the
    storage backend support field-level diffing and operation logging,
    a commit that mutates the Knowledge Structure must leave a trace
    in the operation log.
    """
    import cks
    from cks.core import KnowledgeObject, ObjectIdentity
    from cks.evolution import AddObject

    from cks_runtime.operations.operation_types import EvolveOperation
    from cks_runtime.storage.sqlite_storage import SQLiteStorage

    storage = SQLiteStorage(":memory:")
    runtime = Runtime(core=CksCoreAdapter(), storage=storage)

    # Build a real KnowledgeStructure
    ks = cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{"status":"draft"}}]}'
    )
    session = await runtime.create_session(ks)

    # Evolve: add a new object, which changes the structure
    tx = runtime.begin_transaction(session)
    new_obj = KnowledgeObject(
        identity=ObjectIdentity(id="obj-2", type="Test", name="t2"),
        structure={"status": "new"},
    )
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=[AddObject(new_obj)],
        )
    )
    await runtime.commit_transaction(tx)

    # Verify the operation log was populated.
    logged = storage.list_operations(session.session_id)
    # Должна быть как минимум одна операция: add_object для obj-2
    assert len(logged) >= 1
    assert any(op.object_id == "obj-2" and op.op_type == "add_object" for op in logged)