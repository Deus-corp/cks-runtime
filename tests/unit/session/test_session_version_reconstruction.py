"""
Tests for RuntimeSession.get_version_state() and the CoreInterface.hash()
capability it relies on.

Uses the real ``cks-core`` package (not mocks) so the diff/evolve round
trip is exercised against actual KnowledgeStructure/compose semantics,
not an assumption about them.
"""

from __future__ import annotations

import cks
import pytest

from cks_runtime.core_api.bridge import CoreBridge
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_manager import VersionManager
from cks_runtime_plugins.cks_core.adapter import CksCoreAdapter


def make_structure(ids: list[str]) -> "cks.KnowledgeStructure":
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


@pytest.fixture
def bridge() -> CoreBridge:
    return CoreBridge(implementation=CksCoreAdapter())


@pytest.fixture
def session_with_history(bridge: CoreBridge) -> RuntimeSession:
    """
    A session with 6 committed versions, each adding one object,
    built through the real VersionManager (mirrors what
    ExecutionPipeline._create_version does).
    """
    session = RuntimeSession(knowledge_structure=make_structure(["x0"]))
    versions = VersionManager()

    # v0: initial state already set above, record it as version 0.
    versions.create(session, core_bridge=bridge)

    for step in range(1, 6):
        ids = [f"x{i}" for i in range(step + 1)]
        session.knowledge_structure = make_structure(ids)
        versions.create(session, core_bridge=bridge)

    return session


# ----------------------------------------------------------------------
# CoreInterface.hash() / CoreBridge.hash()
# ----------------------------------------------------------------------


def test_cks_core_adapter_hash_matches_root_hash():
    adapter = CksCoreAdapter()
    structure = make_structure(["a", "b"])
    assert adapter.hash(structure) == structure.root_hash


def test_bridge_hash_delegates_to_adapter(bridge: CoreBridge):
    structure = make_structure(["a"])
    assert bridge.hash(structure) == structure.root_hash


def test_bridge_hash_without_core_raises_runtime_error():
    bridge = CoreBridge(implementation=None)
    with pytest.raises(RuntimeError):
        bridge.hash(object())


def test_core_interface_default_hash_is_not_implemented():
    class MinimalCore(CoreInterface):
        """A Core plugin that never overrides hash()."""

        def validate(self, knowledge_structure, *, extra_constraints=None):
            raise NotImplementedError

        def evolve(self, knowledge_structure, operation):
            raise NotImplementedError

        def serialize(self, knowledge_structure):
            raise NotImplementedError

        def explain(self, knowledge_structure):
            raise NotImplementedError

        def diff(self, source, target):
            raise NotImplementedError

    core = MinimalCore()
    with pytest.raises(NotImplementedError):
        core.hash(object())

    bridge = CoreBridge(implementation=core)
    assert bridge.supports_hash is False
    with pytest.raises(NotImplementedError):
        bridge.hash(object())


def test_bridge_supports_hash_true_for_cks_core(bridge: CoreBridge):
    assert bridge.supports_hash is True


# ----------------------------------------------------------------------
# VersionManager wiring
# ----------------------------------------------------------------------


def test_version_manager_populates_state_hash_when_bridge_given(bridge):
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    version = VersionManager().create(session, core_bridge=bridge)

    assert version.state_hash == session.knowledge_structure.root_hash


def test_version_manager_leaves_state_hash_none_without_bridge():
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    version = VersionManager().create(session)

    assert version.state_hash is None


# ----------------------------------------------------------------------
# RuntimeSession.get_version_state()
# ----------------------------------------------------------------------


def test_get_version_state_reconstructs_via_diff_and_evolve(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    """
    With the default snapshot_every=10 and only 6 versions, every
    lookup after the first forces a full replay from version 0
    through core_bridge.diff()/evolve() -- there is no "cheat" path
    that just returns the stored structure directly.
    """
    target_version = session_with_history.version_history[4]

    reconstructed = session_with_history.get_version_state(
        target_version.version_id,
        bridge,
    )

    assert reconstructed.root_hash == target_version.knowledge_structure.root_hash
    assert reconstructed.root_hash == target_version.state_hash


def test_get_version_state_matches_directly_stored_state_for_every_version(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    for version in session_with_history.version_history:
        reconstructed = session_with_history.get_version_state(
            version.version_id,
            bridge,
            snapshot_every=2,  # forces at least one replay hop for most versions
        )
        assert reconstructed.root_hash == version.knowledge_structure.root_hash


def test_get_version_state_unknown_version_raises(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    with pytest.raises(ValueError, match="not found"):
        session_with_history.get_version_state("does-not-exist", bridge)


def test_get_version_state_rejects_non_positive_snapshot_every(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    target = session_with_history.version_history[0]
    with pytest.raises(ValueError, match="snapshot_every"):
        session_with_history.get_version_state(
            target.version_id, bridge, snapshot_every=0
        )


def test_get_version_state_detects_tampered_history(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    """
    If a stored intermediate state was corrupted/tampered with, the
    replayed reconstruction diverges from the recorded state_hash and
    get_version_state must raise rather than silently return a wrong
    (but internally-consistent-looking) structure.
    """
    target_version = session_with_history.version_history[-1]

    # Corrupt an intermediate stored state so replaying through it
    # produces a different result than what was originally committed.
    session_with_history.version_history[2] = session_with_history.version_history[
        2
    ].__class__(
        session_id=session_with_history.version_history[2].session_id,
        transaction_id=session_with_history.version_history[2].transaction_id,
        knowledge_structure=make_structure(["tampered"]),
        metadata=session_with_history.version_history[2].metadata,
        version_id=session_with_history.version_history[2].version_id,
        created_at=session_with_history.version_history[2].created_at,
        state_hash=session_with_history.version_history[2].state_hash,
    )

    with pytest.raises(ValueError, match="does not match its recorded hash"):
        session_with_history.get_version_state(
            target_version.version_id,
            bridge,
        )