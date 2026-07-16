from cks_runtime import Runtime
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class RecordingCore(CoreInterface):

    def __init__(self):
        self.calls = 0

    def validate(
        self,
        knowledge_structure,
    ):
        self.calls += 1
        return RuntimeValidationResult(valid=True)

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


def test_commit_calls_core_validation():

    core = RecordingCore()

    runtime = Runtime(
        core=core,
    )

    session = runtime.create_session({})

    tx = runtime.begin_transaction(
        session,
    )

    runtime.commit_transaction(tx)

    assert core.calls == 1