import pytest

from cks_runtime.core.interfaces import CoreInterface


class CoreStub(CoreInterface):

    def validate(self, knowledge_structure):
        return {"valid": True}

    def serialize(self, knowledge_structure):
        return knowledge_structure

    def evolve(self, knowledge_structure, operation):
        return knowledge_structure

    def explain(self, knowledge_structure):
        return {}


@pytest.fixture
def core_stub():
    return CoreStub()