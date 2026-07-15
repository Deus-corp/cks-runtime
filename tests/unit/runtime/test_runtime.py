from cks_runtime import Runtime
from cks_runtime import RuntimeConfig

from cks_runtime.core_api.interfaces import CoreInterface


class CoreStub(CoreInterface):

    def validate(self, knowledge_structure):
        return {"valid": True}

    def serialize(self, knowledge_structure):
        return knowledge_structure

    def evolve(
        self,
        knowledge_structure,
        operation,
    ):
        return knowledge_structure

    def explain(self, knowledge_structure):
        return {}


def test_runtime_creation():

    runtime = Runtime()

    assert runtime.sessions is not None
    assert runtime.transactions is not None
    assert runtime.versions is not None
    assert runtime.storage is not None
    assert runtime.diagnostics is not None
    assert runtime.core is None


def test_runtime_configuration():

    runtime = Runtime()

    assert isinstance(
        runtime.config,
        RuntimeConfig,
    )


def test_runtime_accepts_custom_config():

    config = RuntimeConfig()

    runtime = Runtime(config=config)

    assert runtime.config is config


def test_runtime_without_core():

    runtime = Runtime()

    assert runtime.core is None


def test_runtime_with_core():

    core = CoreStub()

    runtime = Runtime(core=core)

    assert runtime.core is core