"""
Tests for delta (patch-only) version storage and reconstruction.
"""

import pytest
import cks
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_manager import VersionManager
from cks_runtime_plugins.cks_core import CksCoreAdapter
from cks_runtime.core_api.bridge import CoreBridge


def make_structure(ids: list[str]):
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def test_first_version_is_always_snapshot():
    bridge = CoreBridge(CksCoreAdapter())
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    version = VersionManager().create(session, core_bridge=bridge)
    assert version.is_snapshot
    assert version.knowledge_structure is not None
    assert version.patch is None


def test_delta_version_stores_patch_not_structure():
    bridge = CoreBridge(CksCoreAdapter())
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    VersionManager().create(session, core_bridge=bridge)  # v0 snapshot

    previous_state = session.knowledge_structure
    session.knowledge_structure = make_structure(["a", "b"])
    version = VersionManager().create(session, core_bridge=bridge, previous_state=previous_state)

    assert not version.is_snapshot
    assert version.knowledge_structure is None
    assert version.patch is not None
    assert len(version.patch) == 1


def test_delta_version_reconstructs_correctly():
    bridge = CoreBridge(CksCoreAdapter())
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    VersionManager().create(session, core_bridge=bridge)  # v0

    previous_state = session.knowledge_structure
    session.knowledge_structure = make_structure(["a", "b"])
    VersionManager().create(session, core_bridge=bridge, previous_state=previous_state)  # v1 delta

    reconstructed = session.get_version_state(
        session.version_history[-1].version_id,
        bridge,
    )
    assert reconstructed.root_hash == session.knowledge_structure.root_hash


def test_delta_version_requires_core_bridge_for_reconstruction():
    bridge = CoreBridge(CksCoreAdapter())
    session = RuntimeSession(knowledge_structure=make_structure(["a"]))
    VersionManager().create(session, core_bridge=bridge)

    previous_state = session.knowledge_structure
    session.knowledge_structure = make_structure(["a", "b"])
    version = VersionManager().create(session, core_bridge=bridge, previous_state=previous_state)

    with pytest.raises(ValueError, match="requires a core_bridge"):
        session.get_version_state(version.version_id, None)