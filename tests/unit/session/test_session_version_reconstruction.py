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

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.core_api.bridge import CoreBridge
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime.versioning.version_manager import VersionManager


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
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
    session = RuntimeSession(knowledge_structure=make_structure(["x0"]))
    versions = VersionManager()

    versions.create(session, core_bridge=bridge)  # v0 snapshot

    for step in range(1, 6):
        previous_state = session.knowledge_structure  # фиксируем до изменения
        ids = [f"x{i}" for i in range(step + 1)]
        session.knowledge_structure = make_structure(ids)
        versions.create(session, core_bridge=bridge, previous_state=previous_state)

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
    target_version = session_with_history.version_history[4]  # delta version

    reconstructed = session_with_history.get_version_state(
        target_version.version_id,
        bridge,
    )

    # The target version's knowledge_structure is None, so we verify
    # against the state_hash instead.
    assert target_version.state_hash is not None
    assert reconstructed.root_hash == target_version.state_hash


def test_get_version_state_matches_directly_stored_state_for_every_version(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    for version in session_with_history.version_history:
        reconstructed = session_with_history.get_version_state(
            version.version_id,
            bridge,
        )
        assert version.state_hash is not None
        assert reconstructed.root_hash == version.state_hash


def test_get_version_state_unknown_version_raises(
    session_with_history: RuntimeSession, bridge: CoreBridge
):
    with pytest.raises(ValueError, match="not found"):
        session_with_history.get_version_state("does-not-exist", bridge)


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


# ----------------------------------------------------------------------
# Regression: replaying a patch whose AddObject targets an id that's
# already present in the state it's replayed on top of must not crash
# reconstruction (cks.evolution.AddObject._mutate raises a bare
# ValueError('Object ... already exists.') otherwise).
# ----------------------------------------------------------------------


def _make_session_with_replay_collision(*, conflicting: bool) -> tuple[RuntimeSession, CoreBridge]:
    """
    Build a two-version session by hand where v1's stored patch
    contains an AddObject for an id ("shared") that's already present
    in v0's snapshot -- simulating a base version reconstruction that
    shares object ids with the structure a patch was originally
    captured against.

    ``conflicting=False``: the AddObject's object is identical to
    what's already there (a clean replay artefact).
    ``conflicting=True``: it differs (a genuine conflict that must be
    surfaced, not silently resolved).
    """
    bridge = CoreBridge(implementation=CksCoreAdapter())

    v0_structure = make_structure(["shared", "other"])
    v0 = RuntimeVersion(
        session_id="s1",
        transaction_id="tx0",
        knowledge_structure=v0_structure,
        metadata={},
        version_id="v0",
        state_hash=bridge.hash(v0_structure),
    )

    shared_replay_value = "different" if conflicting else "shared"
    replayed_obj = cks.KnowledgeObject(
        cks.ObjectIdentity(id="shared", type="Thing", name=shared_replay_value),
    )
    patch = [cks.AddObject(replayed_obj)]

    # The correct end state after this patch is applied is the same
    # as v0 for the "shared" object (the AddObject is a stale replay
    # step, whether or not its payload matches) -- construct that
    # directly and hash it, exactly as VersionManager would have when
    # the version was first recorded.
    v1_structure = v0_structure
    v1 = RuntimeVersion(
        session_id="s1",
        transaction_id="tx1",
        knowledge_structure=None,
        metadata={},
        version_id="v1",
        state_hash=bridge.hash(v1_structure),
        patch=patch,
    )

    session = RuntimeSession(knowledge_structure=v1_structure, session_id="s1")
    session.version_history = [v0, v1]
    return session, bridge


def test_get_version_state_replay_add_object_identical_duplicate_is_noop():
    session, bridge = _make_session_with_replay_collision(conflicting=False)

    reconstructed = session.get_version_state("v1", bridge)

    assert reconstructed.get("shared").identity.name == "shared"
    assert {o.identity.id for o in reconstructed.objects} == {"shared", "other"}


def test_get_version_state_replay_add_object_conflict_logs_warning_not_raise(caplog):
    session, bridge = _make_session_with_replay_collision(conflicting=True)

    with caplog.at_level("WARNING"):
        reconstructed = session.get_version_state("v1", bridge)

    # The existing ("shared") object wins over the stale replayed
    # ("different") one -- reconstruction succeeds and matches the
    # recorded state_hash rather than crashing.
    assert reconstructed.get("shared").identity.name == "shared"
    assert any(
        "already exists with different content" in message
        for message in caplog.messages
    )


def test_get_version_state_add_object_conflict_for_live_edit_still_raises():
    """
    The dedupe above is specific to reconstruction replay
    (get_version_state). A live evolve() call with a genuine duplicate
    AddObject -- e.g. a user-supplied operation in evolve_knowledge or
    fork_sandbox -- must still raise, not be silently swallowed.
    """
    adapter = CksCoreAdapter()
    structure = make_structure(["shared"])
    duplicate = cks.AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id="shared", type="Thing", name="dup"))
    )

    with pytest.raises(ValueError, match="already exists"):
        adapter.evolve(structure, [duplicate])