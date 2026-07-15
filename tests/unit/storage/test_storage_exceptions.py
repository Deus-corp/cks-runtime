import pytest

from cks_runtime.storage.exceptions import SessionNotFound
from cks_runtime.storage.exceptions import VersionNotFound
from cks_runtime.storage.memory_storage import InMemoryStorage


def test_unknown_session():

    storage = InMemoryStorage()

    with pytest.raises(SessionNotFound):
        storage.load_session("missing")


def test_unknown_version():

    storage = InMemoryStorage()

    with pytest.raises(VersionNotFound):
        storage.load_version("missing")