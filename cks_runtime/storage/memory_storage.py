"""
In-memory Runtime Storage.

Reference RuntimeStorage implementation.

Provides deterministic in-memory persistence for Runtime
objects and is primarily intended for testing.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


class InMemoryStorage(RuntimeStorage):
    """
    Reference RuntimeStorage implementation.

    Persists Runtime state entirely in memory.

    Objects are always deep-copied on both save and load
    to preserve snapshot semantics and avoid shared mutable
    state. This follows the same rationale used by immutable
    RuntimeVersion snapshots. :contentReference[oaicite:0]{index=0}
    """

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}
        self._versions: dict[str, RuntimeVersion] = {}
        self._replica_id: str | None = None
        self._graphs: dict[str, dict] = {}

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        """
        Persist a RuntimeSession.

        Honors the same ``expected_version_id`` CAS contract as
        SQLiteStorage, checked against the previously-saved copy's
        latest version -- see ``RuntimeStorage.save_session``.
        """

        if expected_version_id is not None:
            current = self._sessions.get(session.session_id)
            current_latest = (
                current.version_history[-1].version_id
                if current is not None and current.version_history
                else None
            )
            if current_latest != expected_version_id:
                from cks_runtime.storage.storage import ConcurrentModificationError
                raise ConcurrentModificationError(session.session_id)

        self._sessions[
            session.session_id
        ] = deepcopy(session)

    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Restore a RuntimeSession.

        Returns
        -------
        RuntimeSession | None
            Stored session, if present.
        """

        session = self._sessions.get(
            session_id,
        )

        if session is None:
            return None

        return deepcopy(session)

    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

        return session_id in self._sessions

    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return every persisted RuntimeSession.
        """

        return tuple(deepcopy(s) for s in self._sessions.values())

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a RuntimeVersion.
        """

        self._versions[
            version.version_id
        ] = deepcopy(version)

    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Restore a RuntimeVersion.

        Returns
        -------
        RuntimeVersion | None
            Stored version, if present.
        """

        version = self._versions.get(
            version_id,
        )

        if version is None:
            return None

        return deepcopy(version)

    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Whether a RuntimeVersion exists.
        """

        return version_id in self._versions

    def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return every persisted RuntimeVersion.
        """

        return tuple(deepcopy(v) for v in self._versions.values())

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    def clear(self) -> None:
        """
        Remove every persisted Runtime object.
        """

        self._sessions.clear()
        self._versions.clear()
        self._replica_id = None
        self._graphs.clear()

    #
    # ------------------------------------------------------------------
    # Distributed replication (ADR-008)
    # ------------------------------------------------------------------
    #

    def get_or_create_replica_id(self) -> str | None:
        """
        Return this instance's replica identity, generating one on
        first call. "Persisted" only for the lifetime of this
        InMemoryStorage instance -- there is no process to restart
        into, so per-instance stability is all "durable" can mean
        here. Independent of operation-log support (deliberately
        absent from this backend, see ``test_operation_log_is_unsupported_by_default``):
        a replica_id with no operation log to gossip is inert but
        harmless, matching every other optional capability's
        independence from the others in this class.
        """
        if self._replica_id is None:
            self._replica_id = str(uuid4())
        return self._replica_id

    #
    # ------------------------------------------------------------------
    # Graph registry (Memory Agent v1)
    # ------------------------------------------------------------------
    #

    def register_graph(
        self,
        name: str,
        session_id: str,
        description: str = "",
        tags: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self._graphs.get(name)
        created_at = existing["created_at"] if existing is not None else now
        self._graphs[name] = {
            "name": name,
            "session_id": session_id,
            "description": description,
            "tags": tags,
            "created_at": created_at,
            "updated_at": now,
        }

    def get_graph(self, name: str) -> dict | None:
        entry = self._graphs.get(name)
        return deepcopy(entry) if entry is not None else None

    def list_graphs(self, tag: str | None = None) -> list[dict]:
        entries = list(self._graphs.values())
        if tag is not None:
            entries = [e for e in entries if tag in (e.get("tags") or "")]
        return [deepcopy(e) for e in sorted(entries, key=lambda e: e["updated_at"], reverse=True)]