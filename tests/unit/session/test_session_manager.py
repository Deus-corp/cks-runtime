import pytest

from cks_runtime.session.session_manager import SessionManager


def test_create_session():

    manager = SessionManager()

    session = manager.create_session(
        knowledge_structure={}
    )

    assert session is not None

    assert len(manager.list_sessions()) == 1


def test_get_session():

    manager = SessionManager()

    created = manager.create_session({})

    loaded = manager.get_session(
        created.session_id
    )

    assert loaded is created


def test_close_session():

    manager = SessionManager()

    session = manager.create_session({})

    manager.close_session(
        session.session_id
    )

    assert manager.get_session(
        session.session_id
    ) is None

    assert len(
        manager.list_sessions()
    ) == 0


def test_unknown_session_returns_none():

    manager = SessionManager()

    assert manager.get_session(
        "unknown"
    ) is None