"""
SQLite-backed Runtime Storage using JSON serialization.

Persists sessions and versions as JSON, with knowledge structures
serialized via cks-core (canonical JSON). This avoids pickle and the
associated MappingProxyType issues.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable, Optional, TypeVar

import cks
from cks.core import ObjectIdentity, KnowledgeObject, CanonicalRelation
from cks.evolution import AddObject, AddRelation, RemoveObject, RemoveRelation

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import RuntimeStorage, ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime.storage.storage import OutboxTask

# Retry tuning for transient "database is locked" errors under
# concurrent writers. Mirrors the busy-wait/backoff pattern used for
# multi-process SQLite writers elsewhere (see e.g. an operation-log
# CRDT storage layer using the same PRAGMA busy_timeout + exponential
# backoff combination) rather than inventing a second one here.
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_WRITE_RETRIES = 5
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05

_T = TypeVar("_T")


def _retry_on_locked(fn: Callable[[], _T]) -> _T:
    """
    Run fn(), retrying with exponential backoff if it raises a
    "database is locked" sqlite3.OperationalError. Does NOT retry
    ConcurrentModificationError -- that's a legitimate CAS rejection,
    not transient lock contention, and retrying it blindly here would
    silently overwrite the caller's compare-and-swap semantics.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(_WRITE_RETRIES):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= _WRITE_RETRIES - 1:
                raise
            last_exc = exc
            time.sleep(_WRITE_RETRY_BASE_DELAY_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


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
        self._conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                latest_version_id TEXT
            )
            """
        )
        # Add latest_version_id to an existing sessions table if it's
        # missing (same migration pattern as versions.session_id below).
        cur = self._conn.execute("PRAGMA table_info(sessions)")
        session_cols = [row[1] for row in cur.fetchall()]
        if "latest_version_id" not in session_cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN latest_version_id TEXT")
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_operation_log (
                op_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                object_id TEXT NOT NULL,
                op_type TEXT NOT NULL,
                field_key TEXT,
                field_value TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_operation_log_object
            ON cks_operation_log(session_id, object_id)
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
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
        payload = json.dumps(data, ensure_ascii=False)
        new_latest_version_id = (
            session.version_history[-1].version_id if session.version_history else None
        )

        def _write() -> None:
            if expected_version_id is None:
                # No CAS requested (initial create, rollback, abort):
                # unconditional write, same as before.
                self._conn.execute(
                    "INSERT OR REPLACE INTO sessions (session_id, data, latest_version_id) "
                    "VALUES (?, ?, ?)",
                    (session.session_id, payload, new_latest_version_id),
                )
                self._conn.commit()
                return

            cur = self._conn.execute(
                """
                UPDATE sessions SET data = ?, latest_version_id = ?
                WHERE session_id = ? AND latest_version_id IS ?
                """,
                (payload, new_latest_version_id, session.session_id, expected_version_id),
            )
            if cur.rowcount == 0:
                # Either the session doesn't exist yet, or another
                # writer already advanced it past expected_version_id.
                # Distinguish the two so a first-ever commit on a
                # session that was created without going through this
                # CAS path (expected_version_id=None) isn't rejected.
                exists = self._conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?",
                    (session.session_id,),
                ).fetchone()
                self._conn.rollback()
                if exists is not None:
                    raise ConcurrentModificationError(session.session_id)
                self._conn.execute(
                    "INSERT INTO sessions (session_id, data, latest_version_id) VALUES (?, ?, ?)",
                    (session.session_id, payload, new_latest_version_id),
                )
            self._conn.commit()

        _retry_on_locked(_write)

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
        payload = json.dumps(data, ensure_ascii=False)

        def _write() -> None:
            # Strict INSERT, not OR REPLACE: version_id is a fresh
            # uuid4 per version, so a collision here means two writers
            # generated a version under the same id -- a bug worth
            # surfacing as IntegrityError, not silently overwriting
            # one writer's version with another's.
            self._conn.execute(
                "INSERT INTO versions (version_id, session_id, data) VALUES (?, ?, ?)",
                (version.version_id, version.session_id, payload),
            )
            self._conn.commit()

        _retry_on_locked(_write)

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
        def _write() -> None:
            self._conn.execute("DELETE FROM sessions")
            self._conn.execute("DELETE FROM versions")
            self._conn.commit()

        _retry_on_locked(_write)


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
        def _write() -> None:
            self._conn.execute(
                """
                INSERT INTO cks_outbox_tasks
                    (task_type, session_id, payload, status, next_retry_at)
                VALUES (?, ?, ?, 'PENDING', datetime('now'))
                """,
                (task_type, session_id, payload),
            )
            self._conn.commit()

        _retry_on_locked(_write)


    def dequeue_next_outbox_task(self) -> OutboxTask | None:
        row = self._conn.execute(
            """
            SELECT task_id, task_type, session_id, payload, retry_count
            FROM cks_outbox_tasks
            WHERE status = 'PENDING' AND next_retry_at <= datetime('now')
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return OutboxTask(
            task_id=row[0],
            task_type=row[1],
            session_id=row[2],
            payload=row[3],
            retry_count=row[4],
        )

    def complete_outbox_task(self, task_id: int) -> None:
        def _write() -> None:
            self._conn.execute("DELETE FROM cks_outbox_tasks WHERE task_id = ?", (task_id,))
            self._conn.commit()

        _retry_on_locked(_write)

    def fail_outbox_task(self, task_id: int, retry_count: int, error: str, next_retry_at: str) -> None:
        def _write() -> None:
            self._conn.execute(
                """
                UPDATE cks_outbox_tasks
                SET status = 'PENDING',
                    retry_count = ?,
                    next_retry_at = ?,
                    last_error = ?
                WHERE task_id = ?
                """,
                (retry_count, next_retry_at, error, task_id),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    def save_object_embeddings(self, object_id: str, session_id: str, embedding: bytes) -> None:
        def _write() -> None:
            self._conn.execute(
                "INSERT OR REPLACE INTO cks_object_embeddings (object_id, session_id, embedding) VALUES (?, ?, ?)",
                (object_id, session_id, embedding),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        def _write() -> None:
            self._conn.execute(
                "DELETE FROM cks_object_embeddings WHERE object_id = ? AND session_id = ?",
                (object_id, session_id),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    @property
    def supports_outbox(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Operation log (ADR-007)
    # ------------------------------------------------------------------

    def record_operations(
        self,
        session_id: str,
        version_id: str,
        operations: list[RuntimeFieldOperation],
    ) -> None:
        if not operations:
            return

        rows = [
            (
                session_id,
                version_id,
                op.object_id,
                op.op_type,
                op.field_key,
                json.dumps(op.field_value) if op.op_type == "set_field" else None,
            )
            for op in operations
        ]

        def _write() -> None:
            self._conn.executemany(
                """
                INSERT INTO cks_operation_log
                    (session_id, version_id, object_id, op_type, field_key, field_value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._conn.commit()

        _retry_on_locked(_write)

    def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        """
        Return logged field-level operations for a session (optionally
        filtered to a single object_id), oldest first.

        Foundational read path for the merge fast-path sketched in
        ADR-007 (not yet consumed by MergeOperation there); also used
        directly by tests to assert what a commit logged.
        """
        if object_id is not None:
            query = (
                "SELECT object_id, op_type, field_key, field_value, version_id "
                "FROM cks_operation_log WHERE session_id = ? AND object_id = ? "
                "ORDER BY op_id"
            )
            params: tuple = (session_id, object_id)
        else:
            query = (
                "SELECT object_id, op_type, field_key, field_value, version_id "
                "FROM cks_operation_log WHERE session_id = ? ORDER BY op_id"
            )
            params = (session_id,)

        rows = self._conn.execute(query, params).fetchall()
        return [
            RuntimeFieldOperation(
                object_id=row[0],
                op_type=row[1],
                field_key=row[2],
                # delete_field never carries a serialized value (see
                # record_operations); row[3] is NULL for it same as
                # for add/remove_*, so this stays a plain null check.
                field_value=json.loads(row[3]) if row[3] is not None else None,
                version_id=row[4],
            )
            for row in rows
        ]

    @property
    def supports_operation_log(self) -> bool:
        return True


    def search_embeddings(
        self,
        query_embedding: bytes,
        session_id: str,
        top_k: int = 5,
    ) -> list[str]:
        """
        Return object_ids of the top_k closest embeddings to query_embedding
        within the given session. Assumes both query and stored vectors are normalized.
        """
        rows = self._conn.execute(
            "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        import array

        q = array.array("f")
        q.frombytes(query_embedding)

        def score(emb: bytes) -> float | None:
            v = array.array("f")
            v.frombytes(emb)
            if len(v) != len(q):
                # A dimension mismatch means this row was embedded by
                # a different model/provider than the query (e.g. the
                # embedding client was swapped after this object was
                # indexed). zip(v, q) would otherwise silently
                # truncate to the shorter vector and return a
                # meaningless dot product instead of an error --
                # excluding the row is the safe choice, since there is
                # no correct distance to compute between vectors from
                # different embedding spaces.
                return None
            # Dot product = cosine similarity for normalized vectors
            return 1.0 - sum(a * b for a, b in zip(v, q))

        scored = sorted(
            (s, oid)
            for oid, emb in ((r[0], r[1]) for r in rows)
            if (s := score(emb)) is not None
        )
        return [oid for _, oid in scored[:top_k]]