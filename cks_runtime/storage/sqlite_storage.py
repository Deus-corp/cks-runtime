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
                session_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        # Add session_id column to existing versions table if it's missing
        cur = self._conn.execute("PRAGMA table_info(versions)")
        cols = [row[1] for row in cur.fetchall()]
        if "session_id" not in cols:
            self._conn.execute("ALTER TABLE versions ADD COLUMN session_id TEXT")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_outbox_tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON cks_outbox_tasks(status, next_retry_at)
            WHERE status IN ('PENDING', 'FAILED')
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_object_embeddings (
                object_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
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

        # Restore version history from the versions table
        # (sort by created_at in Python to avoid relying on SQLite's JSON operator)
        version_rows = self._conn.execute(
            "SELECT data FROM versions WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        versions: list[RuntimeVersion] = []
        from datetime import datetime
        for (version_json,) in version_rows:
            vdata = json.loads(version_json)
            ks_v = cks.parse(vdata["knowledge_structure"]) if vdata["knowledge_structure"] else None
            patch_v = _deserialize_operators(vdata["patch"]) if vdata.get("patch") else None
            created_at = datetime.fromisoformat(vdata["created_at"])
            version = RuntimeVersion(
                session_id=vdata["session_id"],
                transaction_id=vdata["transaction_id"],
                knowledge_structure=ks_v,
                metadata=vdata["metadata"],
                version_id=vdata["version_id"],
                created_at=created_at,
                state_hash=vdata.get("state_hash"),
                patch=patch_v,
            )
            versions.append(version)

        # Sort chronologically and add to session
        versions.sort(key=lambda v: v.created_at)
        for version in versions:
            session.add_version(version)

        return session

    def has_session(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def list_sessions(self) -> tuple[RuntimeSession, ...]:
        rows = self._conn.execute("SELECT session_id FROM sessions").fetchall()
        sessions = []
        for (sid,) in rows:
            session = self.load_session(sid)
            if session is not None:
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
            "INSERT OR REPLACE INTO versions (version_id, session_id, data) VALUES (?, ?, ?)",
            (version.version_id, version.session_id, json.dumps(data, ensure_ascii=False)),
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
        """Legacy method for embedding projection tasks."""
        import json
        self.enqueue_task(
            task_type="projection",
            session_id=session_id,
            payload=json.dumps({
                "previous_version_id": previous_version_id,
                "new_version_id": new_version_id,
            }),
        )

    def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        """Enqueue a generic background task."""
        self._conn.execute(
            """
            INSERT INTO cks_outbox_tasks
                (task_type, session_id, payload, status, next_retry_at)
            VALUES (?, ?, ?, 'PENDING', datetime('now'))
            """,
            (task_type, session_id, payload),
        )
        self._conn.commit()


    def search_embeddings(
        self,
        query_embedding: bytes,
        session_id: str,
        top_k: int = 5,
    ) -> list[str]:
        """
        Return object_ids of the top_k closest embeddings to query_embedding
        within the given session.
        """
        rows = self._conn.execute(
            "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        # Compute distances
        import struct
        def l1_distance(emb: bytes) -> float:
            sum_dist = 0.0
            for i in range(0, len(emb), 4):
                val_db = struct.unpack("f", emb[i:i+4])[0]
                val_q = struct.unpack("f", query_embedding[i:i+4])[0]
                sum_dist += abs(val_db - val_q)
            return sum_dist

        scored = [(l1_distance(row[1]), row[0]) for row in rows]
        scored.sort(key=lambda x: x[0])
        return [oid for _, oid in scored[:top_k]]