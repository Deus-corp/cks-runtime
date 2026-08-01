"""Unit tests for GossipEnvelope (ADR-008)."""

from __future__ import annotations

import dataclasses
import json

import cks
import pytest

from cks_runtime.gossip.envelope import GossipEnvelope
from cks_runtime.session.session import RuntimeSession

SECRET_A = b"replica-a-secret"
SECRET_B = b"replica-b-secret"


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i)) for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def make_session(ids: list[str] | None = None, **kwargs) -> RuntimeSession:
    if ids is None:
        ids = ["root"]
    return RuntimeSession(knowledge_structure=make_structure(ids), **kwargs)


class TestFromSession:
    def test_round_trips_session_id(self):
        session = make_session(session_id="s1")
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        assert envelope.session_id == "s1"
        assert envelope.sender_replica_id == "r1"
        assert envelope.seq_no == 1

    def test_carries_metadata(self):
        session = make_session(session_id="s1")
        session.metadata["node_id"] = "n1"
        session.metadata["version_vector"] = {"r1": 3}
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        assert envelope.metadata["node_id"] == "n1"
        assert envelope.metadata["version_vector"] == {"r1": 3}

    def test_carries_lineage_pointers(self):
        session = make_session(
            session_id="s1", parent_session_id="parent", parent_version_id="v1"
        )
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        assert envelope.parent_session_id == "parent"
        assert envelope.parent_version_id == "v1"

    def test_generates_a_nonce_when_not_given(self):
        session = make_session()
        e1 = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        e2 = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=2, secret=SECRET_A
        )
        assert e1.nonce != e2.nonce

    def test_accepts_explicit_nonce_and_timestamp_for_determinism(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session,
            sender_replica_id="r1",
            seq_no=1,
            secret=SECRET_A,
            nonce="fixed-nonce",
            timestamp_ms=123456,
        )
        assert envelope.nonce == "fixed-nonce"
        assert envelope.timestamp_ms == 123456


class TestToSession:
    def test_reconstructs_equivalent_knowledge_structure(self):
        session = make_session(["root", "a"], session_id="s1")
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        rebuilt = envelope.to_session()
        assert rebuilt.session_id == "s1"
        assert {o.identity.id for o in rebuilt.knowledge_structure.objects} == {
            "root",
            "a",
        }

    def test_reconstructs_metadata_and_lineage(self):
        session = make_session(
            session_id="s1", parent_session_id="p", parent_version_id="v1"
        )
        session.metadata["node_id"] = "n1"
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        rebuilt = envelope.to_session()
        assert rebuilt.metadata["node_id"] == "n1"
        assert rebuilt.parent_session_id == "p"
        assert rebuilt.parent_version_id == "v1"

    def test_does_not_verify_signature(self):
        # to_session() is a pure reconstruction step -- callers are
        # required to call verify() themselves beforehand.
        session = make_session(session_id="s1")
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        tampered = _replace(envelope, signature="not-a-real-signature")
        # Doesn't raise, even though the signature is bogus.
        rebuilt = tampered.to_session()
        assert rebuilt.session_id == "s1"


class TestVerify:
    def test_accepts_a_correctly_signed_envelope(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        assert envelope.verify(SECRET_A) is True

    def test_rejects_wrong_secret(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        assert envelope.verify(SECRET_B) is False

    def test_rejects_empty_signature(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        tampered = _replace(envelope, signature="")
        assert tampered.verify(SECRET_A) is False

    @pytest.mark.parametrize(
        "field_name,new_value",
        [
            ("session_id", "different-session"),
            ("sender_replica_id", "impersonator"),
            ("knowledge_structure_json", '{"tampered": true}'),
            ("nonce", "different-nonce"),
            ("seq_no", 999),
            ("timestamp_ms", 999),
        ],
    )
    def test_rejects_tampering_with_any_signed_field(self, field_name, new_value):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        tampered = _replace(envelope, **{field_name: new_value})
        assert tampered.verify(SECRET_A) is False

    def test_rejects_tampering_with_metadata(self):
        session = make_session()
        session.metadata["node_id"] = "n1"
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        tampered = _replace(envelope, metadata={"node_id": "attacker-controlled"})
        assert tampered.verify(SECRET_A) is False


class TestWireRoundTrip:
    def test_to_dict_from_dict_round_trip_preserves_signature_validity(self):
        session = make_session(["root", "a"], session_id="s1")
        session.metadata["node_id"] = "n1"
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )

        wire = envelope.to_dict()
        rebuilt = GossipEnvelope.from_dict(wire)

        assert rebuilt == envelope
        assert rebuilt.verify(SECRET_A) is True

    def test_to_dict_is_json_safe(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        # Should not raise -- every value is a JSON primitive.
        json.dumps(envelope.to_dict())

    def test_from_dict_raises_on_missing_required_key(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        wire = envelope.to_dict()
        del wire["signature"]
        with pytest.raises(KeyError):
            GossipEnvelope.from_dict(wire)

    def test_from_dict_defaults_missing_version(self):
        session = make_session()
        envelope = GossipEnvelope.from_session(
            session, sender_replica_id="r1", seq_no=1, secret=SECRET_A
        )
        wire = envelope.to_dict()
        del wire["version"]
        rebuilt = GossipEnvelope.from_dict(wire)
        assert rebuilt.version == envelope.version


def _replace(envelope: GossipEnvelope, **changes) -> GossipEnvelope:
    """Build a modified copy of a frozen GossipEnvelope for tamper tests."""
    return dataclasses.replace(envelope, **changes)