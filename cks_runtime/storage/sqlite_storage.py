"""
SQLite-backed Runtime Storage using JSON serialization.

Persists sessions and versions as JSON, with knowledge structures
serialized via cks-core (canonical JSON). This avoids pickle and the
associated MappingProxyType issues.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Concatenate
from uuid import uuid4

import cks
import numpy as np

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.patch_codec import deserialize_operators, serialize_operators
from cks_runtime.storage.storage import (
    ConcurrentModificationError,
    OutboxTask,
    RuntimeStorage,
)
from cks_runtime.versioning.version import RuntimeVersion

# Retry tuning for transient "database is locked" errors under
# concurrent writers. Mirrors the busy-wait/backoff pattern used for
# multi-process SQLite writers elsewhere (see e.g. an operation-log
# CRDT storage layer using the same PRAGMA busy_timeout + exponential
# backoff combination) rather than inventing a second one here.
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_WRITE_RETRIES = 5
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05


def _retry_on_locked[T](fn: Callable[[], T]) -> T:
    """
    Run fn(), retrying with exponential backoff if it raises a
    "database is locked" sqlite3.OperationalError. Does NOT retry
    ConcurrentModificationError -- that's a legitimate CAS rejection,
    not transient lock contention, and retrying it blindly here would
    silently overwrite the caller's compare-and-swap semantics.
    """
    last_exc: BaseException | None = None
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
# SQLiteStorage
# ---------------------------------------------------------------------------

def _synchronized[**P, T](fn: Callable[Concatenate[SQLiteStorage, P], T]) -> Callable[Concatenate[SQLiteStorage, P], T]:
    """
    Serialize every call to a decorated ``SQLiteStorage`` method through
    ``self._lock`` (a ``threading.RLock``).

    ``self._conn`` (one ``sqlite3.Connection``, opened with
    ``check_same_thread=False``) is reached by every synchronous
    storage call, but those calls do not all run on the same OS
    thread: ``SyncStorageAdapter``/``async_storage.py`` dispatches
    each one via ``asyncio.to_thread``, so e.g. the background
    ``OutboxEmbeddingWorker``'s own poll loop (``dequeue_next_outbox_task``
    every ``poll_interval`` seconds) and a concurrent ``claim_conflict_task``
    MCP tool call (also ``dequeue_next_outbox_task``, different
    ``task_type``) can genuinely execute on two different threads at
    the same wall-clock instant. ``check_same_thread=False`` only
    disables Python's same-thread *ownership* check; it does not make
    concurrent use of one ``Connection``/its implicit cursor safe --
    Python's ``sqlite3`` module keeps non-thread-safe per-connection
    state (e.g. the last-executed-statement bookkeeping ``commit()``
    relies on), so two threads calling ``execute()``/``commit()`` on
    the same ``Connection`` object at once can corrupt that state.
    Confirmed by direct repro: a ``ThreadPoolExecutor`` hammering one
    ``SQLiteStorage`` instance's ``dequeue_next_outbox_task``/
    ``complete_outbox_task`` concurrently reliably produces
    ``sqlite3.OperationalError: cannot commit transaction - SQL
    statements in progress`` and ``sqlite3.InterfaceError: bad
    parameter or other API misuse`` -- the exact error a real
    ``cks-fork-agent`` run hit inside ``dequeue_next_outbox_task``
    while its own ``OutboxEmbeddingWorker`` was polling concurrently
    on the same connection.

    ``RLock`` (not a plain ``Lock``) because some decorated methods
    call another decorated method on ``self`` internally (e.g.
    ``list_sessions`` -> ``load_session``, ``enqueue_outbox_task`` ->
    ``enqueue_task``) -- a plain ``Lock`` would deadlock the owning
    thread on the second acquisition.
    """

    @functools.wraps(fn)
    def wrapper(self: SQLiteStorage, *args: P.args, **kwargs: P.kwargs) -> T:
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class SQLiteStorage(RuntimeStorage):
    """Persists Runtime state in a SQLite database using JSON."""

    def __init__(self, db_path: str = "cks_runtime.db") -> None:
        # RLock, not Lock: several decorated methods below call another
        # decorated method on self internally (list_sessions ->
        # load_session, enqueue_outbox_task -> enqueue_task) -- see
        # _synchronized's docstring above for why this lock exists at
        # all. Must be set before _create_tables() runs, since that
        # method is itself decorated.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        self._create_tables()

    @_synchronized
    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                latest_version_id TEXT,
                modified_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Add latest_version_id to an existing sessions table if it's
        # missing (same migration pattern as versions.session_id below).
        cur = self._conn.execute("PRAGMA table_info(sessions)")
        session_cols = [row[1] for row in cur.fetchall()]
        if "latest_version_id" not in session_cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN latest_version_id TEXT")
        # Add modified_at for GC policy (added in cks-runtime 1.23.1).
        if "modified_at" not in session_cols:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN modified_at TEXT "
                "NOT NULL DEFAULT (datetime('now'))"
            )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_modified_at
            ON sessions(modified_at)
            """
        )
        # Archive table: closed / evicted sessions preserved for audit.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archive_sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                latest_version_id TEXT,
                modified_at TEXT,
                archived_at TEXT NOT NULL DEFAULT (datetime('now'))
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
                claimed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Add claimed_at to pre-existing databases created before it existed.
        cur = self._conn.execute("PRAGMA table_info(cks_outbox_tasks)")
        outbox_cols = [row[1] for row in cur.fetchall()]
        if "claimed_at" not in outbox_cols:
            self._conn.execute("ALTER TABLE cks_outbox_tasks ADD COLUMN claimed_at TEXT")
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON cks_outbox_tasks(status, next_retry_at, claimed_at)
            WHERE status IN ('PENDING', 'FAILED', 'IN_PROGRESS')
            """
        )
        # BUG-01 fix: PRIMARY KEY must be (object_id, session_id) — not
        # just object_id.  object_id is only unique within one session;
        # two different sessions can have an object with the same id
        # (e.g. "earth", "user-1").  With a plain `object_id PRIMARY KEY`
        # an INSERT OR REPLACE for session-B silently overwrites the row
        # for session-A, causing data-loss and wrong similarity results.
        #
        # SQLite cannot ALTER TABLE to change a PRIMARY KEY constraint, so
        # we use the standard rename→recreate→copy→drop migration when the
        # old single-column PK is detected at startup.  The detection check
        # is cheap (one PRAGMA read) and is a no-op on a fresh or already-
        # migrated database.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_object_embeddings (
                object_id  TEXT NOT NULL,
                session_id TEXT NOT NULL,
                embedding  BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (object_id, session_id)
            )
            """
        )
        self._migrate_embeddings_pk_if_needed()
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_object_embeddings_session
            ON cks_object_embeddings(session_id)
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_runtime_identity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                replica_id TEXT NOT NULL
            )
            """
        )
        # Graph registry (Memory Agent v1): name -> session_id lookup so
        # a previously-built Knowledge Graph can be found by a memorable
        # name in a later session, instead of being rebuilt from scratch.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_registry (
                name        TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                description TEXT,
                tags        TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Add `public` for the gallery (Memory Agent v2) to an existing
        # graph_registry table if it's missing -- same migration pattern
        # as sessions.latest_version_id above. Defaults to 0 (false) so
        # every pre-existing registered graph stays private, preserving
        # backward compatibility.
        cur = self._conn.execute("PRAGMA table_info(graph_registry)")
        graph_cols = [row[1] for row in cur.fetchall()]
        if "public" not in graph_cols:
            self._conn.execute(
                "ALTER TABLE graph_registry ADD COLUMN public INTEGER NOT NULL DEFAULT 0"
            )
        self._conn.commit()

    @_synchronized
    def _migrate_embeddings_pk_if_needed(self) -> None:
        """
        Detect and fix the legacy single-column PRIMARY KEY on
        ``cks_object_embeddings``.

        Old schema (pre-BUG-01-fix):
            PRIMARY KEY (object_id)          ← wrong: cross-session collision

        New schema:
            PRIMARY KEY (object_id, session_id)  ← correct

        SQLite doesn't support ALTER TABLE … DROP CONSTRAINT, so the
        only migration path is rename → recreate → copy → drop.  The
        detection is fast (one PRAGMA read) and the migration only runs
        once on first startup after the upgrade.
        """
        # PRAGMA index_list returns one row per index; the implicit
        # PRIMARY KEY on a WITHOUT ROWID or a plain INTEGER PK table
        # shows up as "pk" origin in index_info.  For a TEXT PRIMARY KEY
        # SQLite generates an index named "sqlite_autoindex_<table>_1"
        # with exactly one column.  We detect the old schema by counting
        # the columns in that autoindex: 1 → old single-column PK (needs
        # migration); 2 → already the composite PK (no-op).
        pk_col_count = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM pragma_index_info(
                (SELECT name FROM pragma_index_list('cks_object_embeddings')
                 WHERE origin = 'pk'
                 LIMIT 1)
            )
            """
        ).fetchone()[0]

        if pk_col_count != 1:
            # Either already the composite PK (2 cols) or a fresh table
            # with no rows yet — nothing to do.
            return

        # Old single-column PK detected: migrate.
        self._conn.execute(
            "ALTER TABLE cks_object_embeddings RENAME TO cks_object_embeddings_old"
        )
        self._conn.execute(
            """
            CREATE TABLE cks_object_embeddings (
                object_id  TEXT NOT NULL,
                session_id TEXT NOT NULL,
                embedding  BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (object_id, session_id)
            )
            """
        )
        # Copy existing rows.  On the (unlikely) event that the old table
        # already has two rows with the same object_id for different
        # sessions (impossible under the old buggy schema because the PK
        # would have blocked it), INSERT OR REPLACE keeps the latest one
        # rather than raising — deterministic and safe.
        self._conn.execute(
            """
            INSERT OR REPLACE INTO cks_object_embeddings
                (object_id, session_id, embedding, updated_at)
            SELECT object_id, session_id, embedding,
                   COALESCE(updated_at, datetime('now'))
            FROM cks_object_embeddings_old
            """
        )
        self._conn.execute("DROP TABLE cks_object_embeddings_old")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @_synchronized
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
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, data, latest_version_id, modified_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (session.session_id, payload, new_latest_version_id),
                )
                self._conn.commit()
                return

            cur = self._conn.execute(
                """
                UPDATE sessions SET data = ?, latest_version_id = ?, modified_at = datetime('now')
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
                    "INSERT INTO sessions (session_id, data, latest_version_id, modified_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (session.session_id, payload, new_latest_version_id),
                )
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def load_session(self, session_id: str) -> RuntimeSession | None:
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
            patch_v = deserialize_operators(vdata["patch"]) if vdata.get("patch") else None
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

    @_synchronized
    def has_session(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    @_synchronized
    def list_sessions(self) -> tuple[RuntimeSession, ...]:
        rows = self._conn.execute("SELECT session_id FROM sessions").fetchall()
        sessions = []
        for (sid,) in rows:
            session = self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return tuple(sessions)


    @_synchronized
    def list_sessions_modified_before(
        self,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[RuntimeSession]:
        """
        Return sessions whose ``modified_at`` timestamp is older than
        *cutoff*.  Used by the GC policy to find eviction candidates.
        Results are ordered oldest-first so batch processing makes
        steady progress even when the result set exceeds *limit*.
        """
        rows = self._conn.execute(
            "SELECT session_id FROM sessions "
            "WHERE modified_at < ? "
            "ORDER BY modified_at ASC "
            "LIMIT ?",
            (cutoff.strftime('%Y-%m-%d %H:%M:%S'), limit),
        ).fetchall()
        sessions = []
        for (sid,) in rows:
            session = self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return sessions

    @_synchronized
    def list_sessions_modified_since(
        self,
        watermark: datetime,
        limit: int = 1000,
    ) -> list[RuntimeSession]:
        """
        Return sessions whose ``modified_at`` timestamp is at or after
        *watermark*. Used by ``InferenceStalenessSweeper`` (ADR-009)
        to find candidates for a reasoning-staleness re-check.
        Results are ordered oldest-first, same reason as
        ``list_sessions_modified_before``: a caller advancing its own
        watermark only past a fully-drained batch makes steady,
        gap-free progress even when the window exceeds *limit*.
        """
        rows = self._conn.execute(
            "SELECT session_id FROM sessions "
            "WHERE modified_at >= ? "
            "ORDER BY modified_at ASC "
            "LIMIT ?",
            (watermark.strftime('%Y-%m-%d %H:%M:%S'), limit),
        ).fetchall()
        sessions = []
        for (sid,) in rows:
            session = self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return sessions

    @_synchronized
    def archive_session(self, session: RuntimeSession) -> None:
        """
        Copy *session* to ``archive_sessions`` and remove it from the
        active ``sessions`` table together with all of its versions and
        embeddings.  The archived row is a verbatim copy of the live
        row at the moment of archival, so it can be inspected later
        without re-serializing the session.
        """

        def _write() -> None:
            # Copy to archive
            self._conn.execute(
                """
                INSERT OR REPLACE INTO archive_sessions
                    (session_id, data, latest_version_id, modified_at, archived_at)
                SELECT session_id, data, latest_version_id, modified_at, datetime('now')
                FROM sessions
                WHERE session_id = ?
                """,
                (session.session_id,),
            )
            # Cascade-delete dependent rows
            self._conn.execute(
                "DELETE FROM cks_object_embeddings WHERE session_id = ?",
                (session.session_id,),
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session.session_id,),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    @_synchronized
    def save_version(self, version: RuntimeVersion) -> None:
        if version.knowledge_structure is not None:
            ks_json = cks.serialize(version.knowledge_structure)
            patch_json = None
        else:
            ks_json = None
            patch_json = serialize_operators(version.patch) if version.patch else None
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

    @_synchronized
    def load_version(self, version_id: str) -> RuntimeVersion | None:
        row = self._conn.execute(
            "SELECT data FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        ks = cks.parse(data["knowledge_structure"]) if data["knowledge_structure"] else None
        patch = deserialize_operators(data["patch"]) if data["patch"] else None
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

    @_synchronized
    def has_version(self, version_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return row is not None

    @_synchronized
    def list_versions(self) -> tuple[RuntimeVersion, ...]:
        rows = self._conn.execute("SELECT data FROM versions").fetchall()
        versions = []
        for (data_str,) in rows:
            data = json.loads(data_str)
            ks = cks.parse(data["knowledge_structure"]) if data["knowledge_structure"] else None
            patch = deserialize_operators(data["patch"]) if data.get("patch") else None
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

    @_synchronized
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

    @_synchronized
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


    # A claimed (IN_PROGRESS) task whose worker never called
    # complete_outbox_task/fail_outbox_task (crashed or hung) is
    # treated as abandoned after this long and becomes eligible for
    # another worker to claim.
    _OUTBOX_LEASE_TIMEOUT_MODIFIER = "-5 minutes"

    @_synchronized
    def dequeue_next_outbox_task(self, task_type: str | None = None) -> OutboxTask | None:
        """
        Atomically claim and return the next eligible task: a PENDING
        task whose retry delay has elapsed, or an IN_PROGRESS task
        whose lease has gone stale. Claiming (the UPDATE) and reading
        happen in one statement, so two workers polling the same table
        concurrently (e.g. two cks-mcp server processes sharing a
        SQLite file) can never both claim the same task.

        ``task_type``, when given, restricts the candidate set to that
        type only, so e.g. a Critic-agent worker polling for
        ``"gossip_conflict"`` never claims (and fails on) a
        ``"projection"`` task meant for ``OutboxEmbeddingWorker``, and
        vice versa.
        """
        def _write() -> tuple | None:
            if task_type is None:
                row = self._conn.execute(
                    """
                    UPDATE cks_outbox_tasks
                    SET status = 'IN_PROGRESS', claimed_at = datetime('now')
                    WHERE task_id = (
                        SELECT task_id FROM cks_outbox_tasks
                        WHERE (status = 'PENDING' AND next_retry_at <= datetime('now'))
                           OR (status = 'IN_PROGRESS' AND claimed_at <= datetime('now', ?))
                        ORDER BY created_at ASC
                        LIMIT 1
                    )
                    RETURNING task_id, task_type, session_id, payload, retry_count
                    """,
                    (self._OUTBOX_LEASE_TIMEOUT_MODIFIER,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    UPDATE cks_outbox_tasks
                    SET status = 'IN_PROGRESS', claimed_at = datetime('now')
                    WHERE task_id = (
                        SELECT task_id FROM cks_outbox_tasks
                        WHERE task_type = ?
                          AND ((status = 'PENDING' AND next_retry_at <= datetime('now'))
                           OR (status = 'IN_PROGRESS' AND claimed_at <= datetime('now', ?)))
                        ORDER BY created_at ASC
                        LIMIT 1
                    )
                    RETURNING task_id, task_type, session_id, payload, retry_count
                    """,
                    (task_type, self._OUTBOX_LEASE_TIMEOUT_MODIFIER),
                ).fetchone()
            self._conn.commit()
            return row

        row = _retry_on_locked(_write)
        if row is None:
            return None
        return OutboxTask(
            task_id=row[0],
            task_type=row[1],
            session_id=row[2],
            payload=row[3],
            retry_count=row[4],
        )

    @_synchronized
    def complete_outbox_task(self, task_id: int) -> None:
        def _write() -> None:
            self._conn.execute("DELETE FROM cks_outbox_tasks WHERE task_id = ?", (task_id,))
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def fail_outbox_task(self, task_id: int, retry_count: int, error: str, next_retry_at: str) -> None:
        def _write() -> None:
            self._conn.execute(
                """
                UPDATE cks_outbox_tasks
                SET status = 'PENDING',
                    retry_count = ?,
                    next_retry_at = ?,
                    last_error = ?,
                    claimed_at = NULL
                WHERE task_id = ?
                """,
                (retry_count, next_retry_at, error, task_id),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def dead_letter_outbox_task(self, task_id: int, error: str) -> None:
        """
        Permanently mark a task ``DEAD`` -- removed from the eligible
        pool for good (unlike ``fail_outbox_task``, no ``next_retry_at``
        is set), kept in the table for later inspection via
        ``list_dead_letter_tasks``.
        """
        def _write() -> None:
            self._conn.execute(
                """
                UPDATE cks_outbox_tasks
                SET status = 'DEAD',
                    last_error = ?,
                    claimed_at = NULL
                WHERE task_id = ?
                """,
                (error, task_id),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def touch_outbox_task(self, task_id: int) -> bool:
        """
        Renew the lease on an ``IN_PROGRESS`` task by bumping
        ``claimed_at`` to now, so ``dequeue_next_outbox_task``'s
        stale-lease reclaim (``_OUTBOX_LEASE_TIMEOUT_MODIFIER``) doesn't
        fire while a worker is still actively processing it. Only
        touches a row that is still ``IN_PROGRESS`` -- if it's since
        been completed, failed, dead-lettered, or reclaimed by another
        worker, this is a no-op and returns ``False``.
        """
        def _write() -> bool:
            cur = self._conn.execute(
                """
                UPDATE cks_outbox_tasks
                SET claimed_at = datetime('now')
                WHERE task_id = ? AND status = 'IN_PROGRESS'
                """,
                (task_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

        return _retry_on_locked(_write)

    @_synchronized
    def list_tasks_by_type(
        self,
        task_type: str,
        session_id: str | None = None,
        drain: bool = True,
    ) -> list[OutboxTask]:
        """
        Batch peek/drain read over PENDING tasks of ``task_type``,
        oldest first -- see the abstract method's docstring for how
        this differs from ``dequeue_next_outbox_task``. Tasks
        currently claimed (``IN_PROGRESS``) by another worker are never
        returned, mirroring dequeue's own exclusion of in-flight work.
        """
        def _write() -> list[tuple]:
            if session_id is None:
                rows = self._conn.execute(
                    """
                    SELECT task_id, task_type, session_id, payload, retry_count
                    FROM cks_outbox_tasks
                    WHERE task_type = ? AND status = 'PENDING'
                    ORDER BY created_at ASC
                    """,
                    (task_type,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT task_id, task_type, session_id, payload, retry_count
                    FROM cks_outbox_tasks
                    WHERE task_type = ? AND status = 'PENDING' AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (task_type, session_id),
                ).fetchall()
            if drain and rows:
                task_ids = [row[0] for row in rows]
                placeholders = ",".join("?" for _ in task_ids)
                self._conn.execute(
                    f"DELETE FROM cks_outbox_tasks WHERE task_id IN ({placeholders})",
                    task_ids,
                )
            self._conn.commit()
            return rows

        rows = _retry_on_locked(_write)
        return [
            OutboxTask(
                task_id=row[0],
                task_type=row[1],
                session_id=row[2],
                payload=row[3],
                retry_count=row[4],
            )
            for row in rows
        ]

    @_synchronized
    def list_dead_letter_tasks(self, task_type: str | None = None) -> list[OutboxTask]:
        """Return every DEAD-lettered task, oldest first. Never drains."""
        def _write() -> list[tuple]:
            if task_type is None:
                rows = self._conn.execute(
                    """
                    SELECT task_id, task_type, session_id, payload, retry_count, last_error
                    FROM cks_outbox_tasks
                    WHERE status = 'DEAD'
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT task_id, task_type, session_id, payload, retry_count, last_error
                    FROM cks_outbox_tasks
                    WHERE status = 'DEAD' AND task_type = ?
                    ORDER BY created_at ASC
                    """,
                    (task_type,),
                ).fetchall()
            return rows

        rows = _retry_on_locked(_write)
        return [
            OutboxTask(
                task_id=row[0],
                task_type=row[1],
                session_id=row[2],
                payload=row[3],
                retry_count=row[4],
                last_error=row[5],
            )
            for row in rows
        ]

    @_synchronized
    def save_object_embeddings(self, object_id: str, session_id: str, embedding: bytes) -> None:
        def _write() -> None:
            # PRIMARY KEY is now (object_id, session_id), so this
            # INSERT OR REPLACE only replaces a row for the *same*
            # (object_id, session_id) pair — never a row from a
            # different session that happens to share the object_id.
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cks_object_embeddings
                    (object_id, session_id, embedding, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (object_id, session_id, embedding),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
        version_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        """
        Return logged field-level operations for a session (optionally
        filtered to a single object_id and/or a single version_id),
        oldest first.

        Foundational read path for the merge fast-path sketched in
        ADR-007 (not yet consumed by MergeOperation there); also used
        directly by tests to assert what a commit logged. The
        ``version_id`` filter (ADR-008) additionally backs
        ``RuntimeStorage.fetch_operations_since()``'s generic base-class
        implementation, which needs one version's operations at a time
        rather than a whole session's.
        """
        clauses = ["session_id = ?"]
        params: list[str] = [session_id]
        if object_id is not None:
            clauses.append("object_id = ?")
            params.append(object_id)
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(version_id)

        query = (
            "SELECT object_id, op_type, field_key, field_value, version_id "
            "FROM cks_operation_log WHERE "
            + " AND ".join(clauses)
            + " ORDER BY op_id"
        )

        rows = self._conn.execute(query, tuple(params)).fetchall()
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


    # ------------------------------------------------------------------
    # Backup / Disaster Recovery (ADR-012)
    # ------------------------------------------------------------------

    @_synchronized
    def export_storage(self) -> dict:
        """
        Return a JSON-serialisable snapshot of every SQLite table.

        Sessions and versions are exported as their raw ``data`` JSON
        strings (same payload as written by ``save_session`` /
        ``save_version``), making the dump backend-agnostic.
        Embeddings are base64-encoded. Only PENDING and FAILED outbox
        tasks are exported -- IN_PROGRESS, DEAD, and COMPLETED tasks
        are omitted (claimed or terminal).
        """
        import base64
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        sessions = [
            row[0]
            for row in self._conn.execute("SELECT data FROM sessions").fetchall()
        ]
        versions = [
            row[0]
            for row in self._conn.execute("SELECT data FROM versions").fetchall()
        ]
        graphs = [
            self._graph_row_to_dict(row)
            for row in self._conn.execute(
                "SELECT name, session_id, description, tags, created_at, updated_at, public "
                "FROM graph_registry ORDER BY updated_at DESC"
            ).fetchall()
        ]
        embeddings = [
            {
                "object_id": row[0],
                "session_id": row[1],
                "embedding_b64": base64.b64encode(row[2]).decode(),
                "updated_at": row[3],
            }
            for row in self._conn.execute(
                "SELECT object_id, session_id, embedding, updated_at "
                "FROM cks_object_embeddings"
            ).fetchall()
        ]
        outbox_tasks = [
            {
                "task_type": row[0],
                "session_id": row[1],
                "payload": row[2],
                "status": row[3],
                "retry_count": row[4],
                "next_retry_at": row[5],
                "created_at": row[6],
            }
            for row in self._conn.execute(
                "SELECT task_type, session_id, payload, status, retry_count, "
                "next_retry_at, created_at "
                "FROM cks_outbox_tasks "
                "WHERE status IN ('PENDING', 'FAILED')"
            ).fetchall()
        ]

        return {
            "version": 1,
            "exported_at": _dt.now(_UTC).isoformat(),
            "sessions": sessions,
            "versions": versions,
            "graphs": graphs,
            "embeddings": embeddings,
            "outbox_tasks": outbox_tasks,
        }

    @_synchronized
    def import_storage(self, data: dict, mode: str = "merge") -> None:
        """
        Restore a snapshot produced by ``export_storage``.

        ``mode="clear"`` truncates every table first (atomic, wrapped in
        a single transaction). ``mode="merge"`` uses INSERT OR IGNORE to
        skip rows whose primary key already exists.
        """
        import base64 as _b64

        def _write() -> None:
            if mode == "clear":
                self._conn.execute("DELETE FROM cks_outbox_tasks")
                self._conn.execute("DELETE FROM cks_object_embeddings")
                self._conn.execute("DELETE FROM graph_registry")
                self._conn.execute("DELETE FROM versions")
                self._conn.execute("DELETE FROM sessions")

            # Sessions
            for raw in data.get("sessions", []):
                payload = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                d = json.loads(payload)
                self._conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, data, latest_version_id, modified_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (
                        d["session_id"],
                        payload,
                        d.get("version_history_ids", [None])[-1]
                        if d.get("version_history_ids")
                        else None,
                    ),
                )

            # Versions
            for raw in data.get("versions", []):
                payload = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                d = json.loads(payload)
                self._conn.execute(
                    "INSERT OR IGNORE INTO versions (version_id, session_id, data) "
                    "VALUES (?, ?, ?)",
                    (d["version_id"], d["session_id"], payload),
                )

            # Graphs
            for g in data.get("graphs", []):
                self._conn.execute(
                    "INSERT OR IGNORE INTO graph_registry "
                    "(name, session_id, description, tags, public, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        g["name"],
                        g["session_id"],
                        g.get("description", ""),
                        g.get("tags", ""),
                        int(g.get("public", False)),
                        g.get("created_at", ""),
                        g.get("updated_at", ""),
                    ),
                )

            # Embeddings
            for e in data.get("embeddings", []):
                blob = _b64.b64decode(e["embedding_b64"])
                self._conn.execute(
                    "INSERT OR IGNORE INTO cks_object_embeddings "
                    "(object_id, session_id, embedding, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (e["object_id"], e["session_id"], blob, e.get("updated_at", "")),
                )

            # Outbox tasks (only PENDING / FAILED from the dump)
            for t in data.get("outbox_tasks", []):
                self._conn.execute(
                    "INSERT INTO cks_outbox_tasks "
                    "(task_type, session_id, payload, status, retry_count, next_retry_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        t["task_type"],
                        t["session_id"],
                        t["payload"],
                        "PENDING",  # always reset to PENDING on restore
                        0,
                        t.get("next_retry_at", ""),
                    ),
                )

            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def search_embeddings(
        self,
        query_embedding: bytes,
        session_id: str,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """
        Return (object_id, similarity_score) pairs for the top_k closest
        embeddings to query_embedding within the given session, ordered
        from most to least similar. Assumes both query and stored vectors
        are normalized, so cosine similarity reduces to a dot product.

        similarity_score is clamped to [0.0, 1.0], where 1.0 means the
        vectors are identical and 0.0 means unrelated or opposite --
        raw cosine similarity can go negative for normalized vectors, but
        that distinction isn't meaningful for ranking search results, so
        it's clamped up to a single "least similar" floor instead of
        leaking a negative number to callers.

        Scoring is vectorized: every candidate embedding for the session
        is stacked into a single (n, dim) matrix and scored against the
        query in one matrix-vector product, instead of a per-row Python
        loop. That loop -- not the SQL query -- was the dominant cost of
        this call once a session holds more than a few hundred embedded
        objects.
        """
        rows = self._conn.execute(
            "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?",
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        query_vec = np.frombuffer(query_embedding, dtype=np.float32)

        object_ids: list[str] = []
        vectors: list[np.ndarray] = []
        for object_id, emb in rows:
            v = np.frombuffer(emb, dtype=np.float32)
            if v.shape[0] != query_vec.shape[0]:
                # A dimension mismatch means this row was embedded by
                # a different model/provider than the query (e.g. the
                # embedding client was swapped after this object was
                # indexed). Stacking it into the matrix would either
                # raise or force a meaningless truncation -- excluding
                # the row is the safe choice, since there is no correct
                # distance to compute between vectors from different
                # embedding spaces.
                continue
            object_ids.append(object_id)
            vectors.append(v)

        if not vectors:
            return []

        # Cosine similarity = dot product for normalized vectors,
        # computed for every candidate against the query in one call.
        matrix = np.stack(vectors)
        similarities = np.clip(matrix @ query_vec, 0.0, 1.0)

        # Stable sort so ties keep their original (SQL result) order,
        # matching the previous sorted(..., reverse=True) behaviour.
        order = np.argsort(-similarities, kind="stable")[:top_k]
        return [(object_ids[i], float(similarities[i])) for i in order]

    # ------------------------------------------------------------------
    # Distributed replication (ADR-008)
    # ------------------------------------------------------------------

    @_synchronized
    def get_or_create_replica_id(self) -> str | None:
        """
        Return this database's durable replica identity, generating
        and persisting one under the single-row ``cks_runtime_identity``
        table on first call.

        The ``id = 1`` CHECK constraint keeps the table single-row by
        construction: a second ``INSERT`` racing this one violates the
        primary key and is caught below, so a concurrent first call
        from two callers converges on whichever row committed first
        rather than raising or silently creating two identities.
        """

        def _get_or_create() -> str:
            row = self._conn.execute(
                "SELECT replica_id FROM cks_runtime_identity WHERE id = 1"
            ).fetchone()
            if row is not None:
                return str(row[0])

            candidate = str(uuid4())
            try:
                self._conn.execute(
                    "INSERT INTO cks_runtime_identity (id, replica_id) VALUES (1, ?)",
                    (candidate,),
                )
                self._conn.commit()
                return candidate
            except sqlite3.IntegrityError:
                # Another caller won the race; read back what it wrote.
                self._conn.rollback()
                row = self._conn.execute(
                    "SELECT replica_id FROM cks_runtime_identity WHERE id = 1"
                ).fetchone()
                assert row is not None
                return str(row[0])

        return _retry_on_locked(_get_or_create)

    # ------------------------------------------------------------------
    # Graph registry (Memory Agent v1)
    # ------------------------------------------------------------------

    @staticmethod
    def _graph_row_to_dict(row: tuple) -> dict:
        return {
            "name": row[0],
            "session_id": row[1],
            "description": row[2] or "",
            "tags": row[3] or "",
            "created_at": row[4],
            "updated_at": row[5],
            "public": bool(row[6]),
        }

    @_synchronized
    def register_graph(
        self,
        name: str,
        session_id: str,
        description: str = "",
        tags: str = "",
        public: bool = False,
    ) -> None:
        def _write() -> None:
            self._conn.execute(
                """
                INSERT INTO graph_registry (name, session_id, description, tags, public, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(name) DO UPDATE SET
                    session_id = excluded.session_id,
                    description = excluded.description,
                    tags = excluded.tags,
                    public = excluded.public,
                    updated_at = datetime('now')
                """,
                (name, session_id, description, tags, int(public)),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    @_synchronized
    def get_graph(self, name: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT name, session_id, description, tags, created_at, updated_at, public
            FROM graph_registry WHERE name = ?
            """,
            (name,),
        ).fetchone()
        if row is None:
            return None
        return self._graph_row_to_dict(row)

    @_synchronized
    def list_graphs(
        self, tag: str | None = None, public_only: bool = False
    ) -> list[dict]:
        select = (
            "SELECT name, session_id, description, tags, created_at, updated_at, public "
            "FROM graph_registry"
        )
        clauses: list[str] = []
        params: list[object] = []
        if tag is not None:
            clauses.append("tags LIKE ?")
            params.append(f"%{tag}%")
        if public_only:
            clauses.append("public = 1")
        if clauses:
            select += " WHERE " + " AND ".join(clauses)
        select += " ORDER BY updated_at DESC"
        rows = self._conn.execute(select, params).fetchall()
        return [self._graph_row_to_dict(row) for row in rows]