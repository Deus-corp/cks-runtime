"""
SQLite-backed Runtime Storage using JSON serialization.

Persists sessions and versions as JSON, with knowledge structures
serialized via cks-core (canonical JSON). This avoids pickle and the
associated MappingProxyType issues.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import cks
from cks.core import ObjectIdentity, KnowledgeObject, CanonicalRelation
from cks.evolution import AddObject, AddRelation, RemoveObject, RemoveRelation

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


# ---------------------------------------------------------------------------
# Helpers for serializing/deserializing patches (list of StructuralOperators)
# ---------------------------------------------------------------------------

def _serialize_operators(operators: list) -> list[dict]:
    """Convert a list of StructuralOperator instances to JSON-serializable dicts."""
    result = []
    for op in operators:
        if isinstance(op, AddObject):
            obj = op._obj
            result.append({
                "type": "add_object",
                "identity": {
                    "id": obj.identity.id,
                    "type": obj.identity.type,
                    "name": obj.identity.name,
                },
                "structure": dict(obj.structure),
            })
        elif isinstance(op, AddRelation):
            rel = op._relation
            result.append({
                "type": "add_relation",
                "identity": {
                    "id": rel.identity.id,
                    "type": rel.identity.type,
                    "name": rel.identity.name,
                },
                "participants": list(rel.participants),
                "relation_type": rel.relation_type,
                "structure": dict(rel.structure),
            })
        elif isinstance(op, RemoveObject):
            result.append({
                "type": "remove_object",
                "object_id": op._object_id,
            })
        elif isinstance(op, RemoveRelation):
            result.append({
                "type": "remove_relation",
                "relation_id": op._relation_id,
            })
        else:
            raise TypeError(f"Unknown operator type: {type(op)}")
    return result


def _deserialize_operators(data: list[dict]) -> list:
    """Reconstruct StructuralOperators from JSON dicts."""
    operators = []
    for item in data:
        op_type = item["type"]
        if op_type == "add_object":
            identity = ObjectIdentity(**item["identity"])
            obj = KnowledgeObject(identity=identity, structure=item.get("structure", {}))
            operators.append(AddObject(obj))
        elif op_type == "add_relation":
            identity = ObjectIdentity(**item["identity"])
            rel = CanonicalRelation(
                identity=identity,
                participants=item["participants"],
                relation_type=item["relation_type"],
                structure=item.get("structure", {}),
            )
            operators.append(AddRelation(rel))
        elif op_type == "remove_object":
            operators.append(RemoveObject(item["object_id"]))
        elif op_type == "remove_relation":
            operators.append(RemoveRelation(item["relation_id"]))
        else:
            raise ValueError(f"Unknown operator type: {op_type}")
    return operators


# ---------------------------------------------------------------------------
# SQLiteStorage
# ---------------------------------------------------------------------------

class SQLiteStorage(RuntimeStorage):
    """Persists Runtime state in a SQLite database using JSON."""

    def __init__(self, db_path: str = "cks_runtime.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS versions (
                version_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_projection_outbox (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                previous_version_id TEXT,
                new_version_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Index for polling
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON cks_projection_outbox(status, next_retry_at)
            WHERE status IN ('PENDING', 'FAILED')
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(self, session: RuntimeSession) -> None:
        # Serialize knowledge structure to canonical JSON string
        ks_json = cks.serialize(session.knowledge_structure)
        data = {
            "session_id": session.session_id,
            "knowledge_structure": ks_json,
            "metadata": session.metadata,
            "snapshot_interval": session.snapshot_interval,
            "diagnostics": [],  # diagnostics are not persisted by default
            "version_history_ids": [v.version_id for v in session.version_history],
            "parent_session_id": session.parent_session_id,
            "parent_version_id": session.parent_version_id,
            "closed": session.closed,
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, data) VALUES (?, ?)",
            (session.session_id, json.dumps(data, ensure_ascii=False)),
        )
        self._conn.commit()

    def load_session(self, session_id: str) -> Optional[RuntimeSession]:
        row = self._conn.execute(
            "SELECT data FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        # Reconstruct knowledge structure from canonical JSON
        ks = cks.parse(data["knowledge_structure"])
        session = RuntimeSession(
            knowledge_structure=ks,
            session_id=data["session_id"],
            metadata=data.get("metadata", {}),
            snapshot_interval=data.get("snapshot_interval", 10),
        )
        session.closed = data.get("closed", False)
        session.parent_session_id = data.get("parent_session_id")
        session.parent_version_id = data.get("parent_version_id")
        # Version history will be re-attached separately if needed,
        # but for consistency we could store version IDs and expect them
        # to be loaded later.
        return session

    def has_session(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def list_sessions(self) -> tuple[RuntimeSession, ...]:
        rows = self._conn.execute("SELECT data FROM sessions").fetchall()
        sessions = []
        for (data_str,) in rows:
            data = json.loads(data_str)
            ks = cks.parse(data["knowledge_structure"])
            session = RuntimeSession(
                knowledge_structure=ks,
                session_id=data["session_id"],
                metadata=data.get("metadata", {}),
                snapshot_interval=data.get("snapshot_interval", 10),
            )
            session.closed = data.get("closed", False)
            session.parent_session_id = data.get("parent_session_id")
            session.parent_version_id = data.get("parent_version_id")
            sessions.append(session)
        return tuple(sessions)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def save_version(self, version: RuntimeVersion) -> None:
        if version.knowledge_structure is not None:
            ks_json = cks.serialize(version.knowledge_structure)
            patch_json = None
        else:
            ks_json = None
            patch_json = _serialize_operators(version.patch) if version.patch else None
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
        self._conn.execute(
            "INSERT OR REPLACE INTO versions (version_id, data) VALUES (?, ?)",
            (version.version_id, json.dumps(data, ensure_ascii=False)),
        )
        self._conn.commit()

    def load_version(self, version_id: str) -> Optional[RuntimeVersion]:
        row = self._conn.execute(
            "SELECT data FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        ks = cks.parse(data["knowledge_structure"]) if data["knowledge_structure"] else None
        patch = _deserialize_operators(data["patch"]) if data["patch"] else None
        from datetime import datetime
        created_at = datetime.fromisoformat(data["created_at"])
        version = RuntimeVersion(
            session_id=data["session_id"],
            transaction_id=data["transaction_id"],
            knowledge_structure=ks,
            metadata=data["metadata"],
            version_id=data["version_id"],
            created_at=created_at,
            state_hash=data.get("state_hash"),
            patch=patch,
        )
        return version

    def has_version(self, version_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return row is not None

    def list_versions(self) -> tuple[RuntimeVersion, ...]:
        rows = self._conn.execute("SELECT data FROM versions").fetchall()
        versions = []
        for (data_str,) in rows:
            data = json.loads(data_str)
            ks = cks.parse(data["knowledge_structure"]) if data["knowledge_structure"] else None
            patch = _deserialize_operators(data["patch"]) if data.get("patch") else None
            from datetime import datetime
            created_at = datetime.fromisoformat(data["created_at"])
            version = RuntimeVersion(
                session_id=data["session_id"],
                transaction_id=data["transaction_id"],
                knowledge_structure=ks,
                metadata=data["metadata"],
                version_id=data["version_id"],
                created_at=created_at,
                state_hash=data.get("state_hash"),
                patch=patch,
            )
            versions.append(version)
        return tuple(versions)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self._conn.execute("DELETE FROM sessions")
        self._conn.execute("DELETE FROM versions")
        self._conn.commit()


    def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO cks_projection_outbox
                (session_id, previous_version_id, new_version_id)
            VALUES (?, ?, ?)
            """,
            (session_id, previous_version_id, new_version_id),
        )
        self._conn.commit()