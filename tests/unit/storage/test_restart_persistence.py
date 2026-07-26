"""
Test that sessions and versions survive a Runtime restart.
"""

import os
import tempfile
import pytest
import cks
from cks_runtime.runtime import Runtime
from cks_runtime.config import RuntimeConfig
from cks_runtime.operations.operation_types import ValidateOperation
from cks_runtime_plugins.cks_core import CksCoreAdapter


def test_sessions_survive_runtime_restart():
    """Create a session with a version in one Runtime, then verify
    it is still accessible from a second Runtime attached to the same
    storage file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        # Build a minimal, valid Knowledge Structure
        ks = cks.parse(
            '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
        )

        # First Runtime: create session and commit a version
        config = RuntimeConfig(storage_path=path)
        rt1 = Runtime(core=CksCoreAdapter(), config=config)
        session = rt1.create_session(ks)
        tx = rt1.begin_transaction(session)
        tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
        version = rt1.commit_transaction(tx)
        assert session.version_count == 1

        # Second Runtime: same storage, simulate restart
        rt2 = Runtime(core=CksCoreAdapter(), config=RuntimeConfig(storage_path=path))

        # Session must be found
        restored = rt2.get_session(session.session_id)
        assert restored is not None
        assert restored.version_count == 1
        assert restored.version_history[0].version_id == version.version_id

        # list_sessions must include it
        assert session.session_id in [s.session_id for s in rt2.list_sessions()]
    finally:
        os.unlink(path)


def test_closed_session_stays_closed_after_runtime_restart():
    """
    Regression test: Runtime.close_session() used to only update the
    in-memory registry (via SessionManager.close_session(), which
    calls session.close() and discards the session from its dict) --
    it never called storage.save_session(), unlike create_session and
    create_branch. So the storage record kept reflecting closed=False,
    and after a restart _restore_from_storage() would reconstruct the
    session as active again -- reachable via get_session() and,
    because nothing in the transaction/commit pipeline checks
    session.closed before operating, fully operable again too. This
    was true even for a session closed specifically because it had
    just been merged into another one (the merge_branch +
    close_session workflow the README documents).

    A closed session must stay unreachable via get_session() -- in
    the same process (already true before this fix, since
    close_session() removes it from the in-memory registry outright)
    and after a restart (the actual regression).
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        ks = cks.parse(
            '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
        )

        config = RuntimeConfig(storage_path=path)
        rt1 = Runtime(core=CksCoreAdapter(), config=config)
        session = rt1.create_session(ks)
        tx = rt1.begin_transaction(session)
        tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
        rt1.commit_transaction(tx)

        session_id = session.session_id
        rt1.close_session(session_id)

        # Same-process: already unreachable before this fix.
        assert rt1.get_session(session_id) is None

        # Simulate a restart: a fresh Runtime attached to the same storage file.
        rt2 = Runtime(core=CksCoreAdapter(), config=RuntimeConfig(storage_path=path))

        assert rt2.get_session(session_id) is None
        assert session_id not in [s.session_id for s in rt2.list_sessions()]
    finally:
        os.unlink(path)