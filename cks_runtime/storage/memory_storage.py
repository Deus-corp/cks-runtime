"""
In-memory Runtime Storage.

Reference RuntimeStorage implementation.

Provides deterministic in-memory persistence for Runtime
objects and is primarily intended for testing.
"""

from __future__ import annotations

import json
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
        public: bool = False,
        source_graph_name: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self._graphs.get(name)
        created_at = existing["created_at"] if existing is not None else now
        # A plain re-register (source_graph_name=None) must not erase
        # lineage recorded by an earlier clone_graph(copy_name=...) call
        # for this same name -- same rationale as SQLiteStorage.
        resolved_source_graph_name = source_graph_name
        if resolved_source_graph_name is None and existing is not None:
            resolved_source_graph_name = existing.get("source_graph_name")
        self._graphs[name] = {
            "name": name,
            "session_id": session_id,
            "description": description,
            "tags": tags,
            "public": public,
            "source_graph_name": resolved_source_graph_name,
            "created_at": created_at,
            "updated_at": now,
        }

    def get_graph(self, name: str) -> dict | None:
        entry = self._graphs.get(name)
        return deepcopy(entry) if entry is not None else None

    def list_graphs(
        self, tag: str | None = None, public_only: bool = False
    ) -> list[dict]:
        entries = list(self._graphs.values())
        if tag is not None:
            entries = [e for e in entries if tag in (e.get("tags") or "")]
        if public_only:
            entries = [e for e in entries if e.get("public")]
        return [deepcopy(e) for e in sorted(entries, key=lambda e: e["updated_at"], reverse=True)]

    # ------------------------------------------------------------------
    # Backup / Disaster Recovery (ADR-012)
    # ------------------------------------------------------------------

    def export_storage(self) -> dict:
        """
        Return a JSON-serialisable snapshot of every in-memory table.

        Sessions and versions are stored as their canonical JSON strings
        (same format as SQLiteStorage's ``data`` column), so the dump is
        backend-agnostic: an SQLiteStorage can import what an
        InMemoryStorage exported and vice-versa.
        """
        import cks

        exported_sessions: list[str] = []
        for session in self._sessions.values():
            ks_json = cks.serialize(session.knowledge_structure)
            data = {
                "session_id": session.session_id,
                "knowledge_structure": ks_json,
                "metadata": session.metadata,
                "snapshot_interval": session.snapshot_interval,
                "diagnostics": [],
                "version_history_ids": [v.version_id for v in session.version_history],
                "parent_session_id": session.parent_session_id,
                "parent_version_id": session.parent_version_id,
                "closed": session.closed,
            }
            exported_sessions.append(json.dumps(data, ensure_ascii=False))

        exported_versions: list[str] = []
        for version in self._versions.values():
            from cks_runtime.storage.patch_codec import serialize_operators
            if version.knowledge_structure is not None:
                ks_json = cks.serialize(version.knowledge_structure)
                patch_json = None
            else:
                ks_json = None
                patch_json = serialize_operators(version.patch) if version.patch is not None else None
            data = {
                "version_id": version.version_id,
                "session_id": version.session_id,
                "transaction_id": version.transaction_id,
                "knowledge_structure": ks_json,
                "metadata": dict(version.metadata),
                "created_at": version.created_at.isoformat(),
                "state_hash": version.state_hash,
                "patch": patch_json,
            }
            exported_versions.append(json.dumps(data, ensure_ascii=False))

        return {
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "sessions": exported_sessions,
            "versions": exported_versions,
            "graphs": [deepcopy(g) for g in self._graphs.values()],
            "embeddings": [],   # InMemoryStorage doesn't persist embeddings
            "outbox_tasks": [], # InMemoryStorage doesn't persist outbox tasks
        }

    def import_storage(self, data: dict, mode: str = "merge") -> None:
        """
        Restore a snapshot into this in-memory store.

        ``mode="clear"`` wipes existing data first; ``mode="merge"``
        skips sessions/versions/graphs whose primary key already exists.
        """
        from datetime import datetime as _dt

        import cks

        from cks_runtime.session.session import RuntimeSession
        from cks_runtime.storage.patch_codec import deserialize_operators
        from cks_runtime.versioning.version import RuntimeVersion

        if mode == "clear":
            self._sessions.clear()
            self._versions.clear()
            self._graphs.clear()

        for raw in data.get("sessions", []):
            d = json.loads(raw) if isinstance(raw, str) else raw
            sid = d["session_id"]
            if mode == "merge" and sid in self._sessions:
                continue
            ks = cks.parse(d["knowledge_structure"])
            session = RuntimeSession(
                knowledge_structure=ks,
                session_id=sid,
                metadata=d.get("metadata", {}),
                snapshot_interval=d.get("snapshot_interval", 10),
            )
            session.closed = d.get("closed", False)
            session.parent_session_id = d.get("parent_session_id")
            session.parent_version_id = d.get("parent_version_id")
            self._sessions[sid] = session

        for raw in data.get("versions", []):
            d = json.loads(raw) if isinstance(raw, str) else raw
            vid = d["version_id"]
            if mode == "merge" and vid in self._versions:
                continue
            ks = cks.parse(d["knowledge_structure"]) if d.get("knowledge_structure") else None
            patch = deserialize_operators(d["patch"]) if d.get("patch") is not None else None
            created_at = _dt.fromisoformat(d["created_at"])
            version = RuntimeVersion(
                session_id=d["session_id"],
                transaction_id=d["transaction_id"],
                knowledge_structure=ks,
                metadata=d["metadata"],
                version_id=vid,
                created_at=created_at,
                state_hash=d.get("state_hash"),
                patch=patch,
            )
            self._versions[vid] = version

        for graph in data.get("graphs", []):
            name = graph["name"]
            if mode == "merge" and name in self._graphs:
                continue
            self._graphs[name] = deepcopy(graph)