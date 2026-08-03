from datetime import UTC, datetime

from cks_runtime.events.runtime_event import (
    InferenceConflictDetected,
    RuntimeEvent,
    SessionClosed,
    SessionCreated,
    TransactionAborted,
    TransactionCommitted,
    TransactionRolledBack,
    VersionCreated,
)


def test_runtime_event_defaults():

    event = RuntimeEvent()

    assert isinstance(
        event.created_at,
        datetime,
    )

    assert event.created_at.tzinfo is UTC

    assert event.metadata == {}


def test_session_created():

    event = SessionCreated(
        session_id="session-1",
    )

    assert event.session_id == "session-1"

    assert event.metadata == {}


def test_session_closed():

    event = SessionClosed(
        session_id="session-1",
    )

    assert event.session_id == "session-1"


def test_transaction_committed():

    event = TransactionCommitted(
        transaction_id="tx-1",
        session_id="session-1",
    )

    assert event.transaction_id == "tx-1"

    assert event.session_id == "session-1"


def test_transaction_rolled_back():

    event = TransactionRolledBack(
        transaction_id="tx-1",
        session_id="session-1",
    )

    assert event.transaction_id == "tx-1"

    assert event.session_id == "session-1"


def test_transaction_aborted():

    event = TransactionAborted(
        transaction_id="tx-1",
        session_id="session-1",
    )

    assert event.transaction_id == "tx-1"

    assert event.session_id == "session-1"


def test_version_created():

    event = VersionCreated(
        version_id="version-1",
        session_id="session-1",
        transaction_id="tx-1",
    )

    assert event.version_id == "version-1"

    assert event.session_id == "session-1"

    assert event.transaction_id == "tx-1"


def test_inference_conflict_detected():

    event = InferenceConflictDetected(
        session_id="session-1",
        version_id="version-1",
        diagnostics=[
            {
                "code": "CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT",
                "severity": "warning",
                "message": "conflict",
                "location": "step-1",
            }
        ],
    )

    assert event.session_id == "session-1"

    assert event.version_id == "version-1"

    assert event.diagnostics == [
        {
            "code": "CKS-EXT-INFERENCE-CONFIDENCE-CONFLICT",
            "severity": "warning",
            "message": "conflict",
            "location": "step-1",
        }
    ]


def test_inference_conflict_detected_defaults():

    event = InferenceConflictDetected()

    assert event.session_id == ""

    assert event.version_id == ""

    assert event.diagnostics == []


def test_runtime_event_metadata():

    event = SessionCreated(
        session_id="session-1",
        metadata={
            "user": "alice",
        },
    )

    assert event.metadata == {
        "user": "alice",
    }