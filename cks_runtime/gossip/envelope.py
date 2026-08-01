"""
``GossipEnvelope`` -- signed, replay-protected wire format for
exchanging ``RuntimeSession`` snapshots between ``GossipTransport``
peers (ADR-008).

The shape (HMAC-SHA256 over a canonical payload, a nonce and a
monotonic per-sender ``seq_no`` for replay/reordering protection,
``hmac.compare_digest`` for constant-time verification) follows a
pattern used elsewhere for gossip envelope authenticity between P2P
peers. What's adopted here is that shape, not any payload or code --
the fields below (a ``RuntimeSession`` snapshot: its
``KnowledgeStructure``, ``metadata``, lineage pointers) are specific
to this repo and were designed fresh against ``RuntimeSession``
(``cks_runtime/session/session.py``) and the canonical serializer
(``cks.serialize``/``cks.parse``), the same pair every
``RuntimeStorage`` backend already uses to persist a session's
``knowledge_structure`` (see e.g. ``sqlite_storage.py``).

This is deliberately a **separate trust domain** from cks-mcp's
provenance signing (ADR-002 there, ``cks_mcp.provenance``): a valid
``GossipEnvelope`` signature proves "this message really came from
replica X", which is a different claim from cks-mcp's "this fact was
really checked against this URL". See ``secret.py`` for why the
signing secret is never shared between the two.

Authenticity (this module) is orthogonal to mergeability
(``GossipAdapter``, ``adapter.py``): a verified envelope still goes
through the ordinary ``MergeOperation`` fast paths / three-way merge
before it affects local state. Authenticating a *malicious* peer's
edits is explicitly out of scope, matching ADR-008's Non-Goals
("Not solving Byzantine or malicious peers... assumes cooperating
agents within one deployment") -- this envelope only prevents an
unrelated third party from injecting or replaying traffic between
replicas that already trust each other.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import cks

from cks_runtime.session.session import RuntimeSession

#: Field separator used only inside the HMAC signing payload. Chosen
#: as the ASCII "unit separator" control character specifically
#: because it cannot appear in any of the joined fields short of a
#: pathological embedded control character -- unlike "|" (used by
#: ``cks_mcp.provenance``, safe there only because its fields are
#: simple scalars), the knowledge-structure JSON here is
#: attacker-influenced free text and could legitimately contain "|".
_SIGNING_SEPARATOR = "\x1f"

GOSSIP_ENVELOPE_VERSION = "1.0"


@dataclass(slots=True, frozen=True)
class GossipEnvelope:
    """
    A signed snapshot of one ``RuntimeSession``, as sent by
    ``sender_replica_id``.

    Carries exactly what ``GossipAdapter.apply_remote_session`` needs
    to reconstruct a ``RuntimeSession`` (see ``to_session``) plus the
    replay-protection fields ``GossipFilter`` checks. Immutable once
    built -- a received envelope is verified, filtered, and consumed;
    nothing about gossip authenticity requires mutating one in place.
    """

    sender_replica_id: str
    session_id: str
    knowledge_structure_json: str
    metadata: dict[str, Any]
    parent_session_id: str | None
    parent_version_id: str | None
    nonce: str
    seq_no: int
    timestamp_ms: int
    signature: str
    version: str = field(default=GOSSIP_ENVELOPE_VERSION)

    # ------------------------------------------------------------------
    # Session <-> envelope
    # ------------------------------------------------------------------

    @classmethod
    def from_session(
        cls,
        session: RuntimeSession,
        *,
        sender_replica_id: str,
        seq_no: int,
        secret: bytes,
        nonce: str | None = None,
        timestamp_ms: int | None = None,
    ) -> GossipEnvelope:
        """
        Build and sign an envelope carrying ``session``'s current
        state.

        ``nonce``/``timestamp_ms`` are normally left to their
        defaults (a fresh random nonce, the current time); the
        parameters exist so tests can construct deterministic
        envelopes.
        """
        knowledge_structure_json = cks.serialize(session.knowledge_structure)
        resolved_nonce = nonce if nonce is not None else secrets.token_hex(16)
        resolved_timestamp_ms = (
            timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        )

        signature = _sign(
            sender_replica_id=sender_replica_id,
            session_id=session.session_id,
            knowledge_structure_json=knowledge_structure_json,
            metadata=session.metadata,
            parent_session_id=session.parent_session_id,
            parent_version_id=session.parent_version_id,
            nonce=resolved_nonce,
            seq_no=seq_no,
            timestamp_ms=resolved_timestamp_ms,
            secret=secret,
        )

        return cls(
            sender_replica_id=sender_replica_id,
            session_id=session.session_id,
            knowledge_structure_json=knowledge_structure_json,
            metadata=dict(session.metadata),
            parent_session_id=session.parent_session_id,
            parent_version_id=session.parent_version_id,
            nonce=resolved_nonce,
            seq_no=seq_no,
            timestamp_ms=resolved_timestamp_ms,
            signature=signature,
        )

    def to_session(self) -> RuntimeSession:
        """
        Reconstruct the ``RuntimeSession`` this envelope carries.

        Does **not** verify the signature -- callers must call
        ``verify()`` first (``GossipService``/the HTTP handler always
        do, before this is ever called). Only the fields
        ``GossipAdapter.apply_remote_session`` actually reads
        (``session_id``, ``metadata``, ``knowledge_structure``) plus
        the lineage pointers are populated; ``version_history`` stays
        empty, matching every other place a bare snapshot is handed
        to ``apply_remote_session`` (see ``exchange.gossip_exchange``).
        """
        return RuntimeSession(
            knowledge_structure=cks.parse(self.knowledge_structure_json),
            session_id=self.session_id,
            metadata=dict(self.metadata),
            parent_session_id=self.parent_session_id,
            parent_version_id=self.parent_version_id,
        )

    # ------------------------------------------------------------------
    # Signing / verification
    # ------------------------------------------------------------------

    def verify(self, secret: bytes) -> bool:
        """Constant-time check that ``signature`` matches ``secret``."""
        if not self.signature:
            return False
        expected = _sign(
            sender_replica_id=self.sender_replica_id,
            session_id=self.session_id,
            knowledge_structure_json=self.knowledge_structure_json,
            metadata=self.metadata,
            parent_session_id=self.parent_session_id,
            parent_version_id=self.parent_version_id,
            nonce=self.nonce,
            seq_no=self.seq_no,
            timestamp_ms=self.timestamp_ms,
            secret=secret,
        )
        return hmac.compare_digest(expected, self.signature)

    # ------------------------------------------------------------------
    # Wire (de)serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation for an HTTP request/response body."""
        return {
            "version": self.version,
            "sender_replica_id": self.sender_replica_id,
            "session_id": self.session_id,
            "knowledge_structure_json": self.knowledge_structure_json,
            "metadata": self.metadata,
            "parent_session_id": self.parent_session_id,
            "parent_version_id": self.parent_version_id,
            "nonce": self.nonce,
            "seq_no": self.seq_no,
            "timestamp_ms": self.timestamp_ms,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GossipEnvelope:
        """
        Parse a wire payload back into an envelope.

        Deliberately strict about shape (missing/mistyped required
        keys raise ``KeyError``/``TypeError`` rather than silently
        substituting defaults) -- unlike ``VersionVector.from_metadata``,
        a malformed *envelope* is not something the caller should ever
        merge state from, so degrading gracefully here would just
        turn a wire-format bug into a mysterious downstream
        verification failure instead of an immediate, obvious one.
        """
        return cls(
            version=str(data.get("version", GOSSIP_ENVELOPE_VERSION)),
            sender_replica_id=str(data["sender_replica_id"]),
            session_id=str(data["session_id"]),
            knowledge_structure_json=str(data["knowledge_structure_json"]),
            metadata=dict(data["metadata"]) if data.get("metadata") else {},
            parent_session_id=data["parent_session_id"],
            parent_version_id=data["parent_version_id"],
            nonce=str(data["nonce"]),
            seq_no=int(data["seq_no"]),
            timestamp_ms=int(data["timestamp_ms"]),
            signature=str(data["signature"]),
        )


def _sign(
    *,
    sender_replica_id: str,
    session_id: str,
    knowledge_structure_json: str,
    metadata: dict[str, Any],
    parent_session_id: str | None,
    parent_version_id: str | None,
    nonce: str,
    seq_no: int,
    timestamp_ms: int,
    secret: bytes,
) -> str:
    metadata_json = json.dumps(metadata, sort_keys=True, default=str)
    parts = (
        sender_replica_id,
        session_id,
        knowledge_structure_json,
        metadata_json,
        parent_session_id or "",
        parent_version_id or "",
        nonce,
        str(seq_no),
        str(timestamp_ms),
    )
    payload = _SIGNING_SEPARATOR.join(parts).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()