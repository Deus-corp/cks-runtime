"""
PostgreSQL-backed Runtime Storage (async).

Implements ``AsyncRuntimeStorage`` for sessions, versions, outbox,
embeddings (via pgvector), and the operation log.

Design choices vs ``SQLiteStorage``
------------------------------------

JSONB payloads
    psycopg round-trips ``dict`` <-> ``jsonb`` via ``Jsonb(...)``
    automatically, so there is no manual ``json.dumps``/``json.loads``
    pass. JSONB is also indexable (GIN) for future query acceleration.

NULL-safe CAS
    ``IS NOT DISTINCT FROM`` instead of ``=`` so the first-ever commit
    (``expected_version_id=None`` vs a NULL column) is accepted.

Retry scope
    ``_retry_on_transient`` covers ``psycopg.OperationalError`` --
    SerializationFailure, DeadlockDetected, dropped connections.
    ``ConcurrentModificationError`` and ``IntegrityError`` are NOT
    retried: they are legitimate rejections, not transient contention.

Connection pool
    Every method borrows a connection for the duration of the call.
    SQLite can use a single persistent connection because it serialises
    writers itself; Postgres expects a pool of short-lived checkouts.

Outbox (SELECT ... FOR UPDATE SKIP LOCKED)
    Two concurrent workers polling the same table atomically claim
    different tasks: the inner SELECT picks the earliest eligible row,
    FOR UPDATE locks it, SKIP LOCKED prevents the second worker from
    blocking on the same row (it moves to the next one instead).

pgvector embeddings
    Vectors are stored as the native ``vector`` type with an HNSW index
    (``vector_cosine_ops``). Similarity search is a single SQL ORDER BY
    with the ``<=>`` cosine-distance operator -- the HNSW index makes
    this sub-millisecond even for millions of rows.  pgvector must be
    installed as a Postgres extension (``CREATE EXTENSION vector``).

    The vector dimension is discovered at runtime from the first
    embedding inserted and stored in the ``cks_embedding_meta`` table.
    Every subsequent insert is validated against that dimension so a
    model swap is caught early rather than silently producing a corrupt
    index. ``_ensure_embedding_index`` creates the HNSW index lazily on
    first insert after the dimension is known.

list_sessions (N+1 fix)
    A single LEFT JOIN query loads all sessions and their version rows
    in one round-trip instead of one query per session.
"""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

import cks
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.async_storage import AsyncRuntimeStorage
from cks_runtime.storage.patch_codec import deserialize_operators, serialize_operators
from cks_runtime.storage.storage import ConcurrentModificationError, OutboxTask
from cks_runtime.versioning.version import RuntimeVersion

# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------

_WRITE_RETRIES = 5
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05

# An IN_PROGRESS outbox task whose worker never called complete/fail
# (crashed or hung) is treated as abandoned after this interval and
# becomes eligible for another worker to claim.
_OUTBOX_LEASE_TIMEOUT = "5 minutes"


async def _retry_on_transient(fn: Callable[[], Awaitable[Any]]) -> Any:
    """
    Run fn(), retrying with exponential backoff on a transient
    psycopg.OperationalError (SerializationFailure, DeadlockDetected,
    dropped-connection errors).  Does NOT retry ConcurrentModification-
    Error or psycopg.IntegrityError -- both are legitimate rejections.
    """
    last_exc: BaseException | None = None
    for attempt in range(_WRITE_RETRIES):
        try:
            return await fn()
        except psycopg.OperationalError as exc:
            if attempt >= _WRITE_RETRIES - 1:
                raise
            last_exc = exc
            await asyncio.sleep(_WRITE_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# DDL — sessions + versions (unchanged from v1)
# ---------------------------------------------------------------------------

_DDL_SESSIONS = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id        TEXT PRIMARY KEY,
        data              JSONB NOT NULL,
        latest_version_id TEXT,
        modified_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_DDL_VERSIONS = """
    CREATE TABLE IF NOT EXISTS versions (
        version_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        data       JSONB NOT NULL
    )
"""

_DDL_VERSIONS_IDX = """
    CREATE INDEX IF NOT EXISTS idx_versions_session ON versions(session_id)
"""

# ---------------------------------------------------------------------------
# DDL — outbox
# ---------------------------------------------------------------------------

_DDL_OUTBOX = """
    CREATE TABLE IF NOT EXISTS cks_outbox_tasks (
        task_id      BIGSERIAL PRIMARY KEY,
        task_type    TEXT      NOT NULL,
        session_id   TEXT      NOT NULL,
        payload      TEXT      NOT NULL,
        status       TEXT      NOT NULL DEFAULT 'PENDING',
        retry_count  INTEGER   NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_error   TEXT,
        claimed_at   TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_DDL_OUTBOX_IDX = """
    CREATE INDEX IF NOT EXISTS idx_outbox_claimable
    ON cks_outbox_tasks (status, next_retry_at, claimed_at)
    WHERE status IN ('PENDING', 'FAILED', 'IN_PROGRESS')
"""

# ---------------------------------------------------------------------------
# DDL — operation log (ADR-007)
# ---------------------------------------------------------------------------

_DDL_OPLOG = """
    CREATE TABLE IF NOT EXISTS cks_operation_log (
        op_id      BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        object_id  TEXT NOT NULL,
        op_type    TEXT NOT NULL,
        field_key  TEXT,
        field_value TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_DDL_OPLOG_IDX = """
    CREATE INDEX IF NOT EXISTS idx_oplog_session_object
    ON cks_operation_log (session_id, object_id)
"""

# ---------------------------------------------------------------------------
# DDL — runtime identity (ADR-008)
# ---------------------------------------------------------------------------

_DDL_IDENTITY = """
    CREATE TABLE IF NOT EXISTS cks_runtime_identity (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        replica_id TEXT NOT NULL
    )
"""

# ---------------------------------------------------------------------------
# DDL — embedding metadata (dimension registry)
# ---------------------------------------------------------------------------

_DDL_EMBED_META = """
    CREATE TABLE IF NOT EXISTS cks_embedding_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""

# ---------------------------------------------------------------------------
# DDL — embeddings (pgvector, dimension set at runtime)
# ---------------------------------------------------------------------------
# The ``vector(N)`` column cannot use IF NOT EXISTS on the column itself,
# so the table creation is split: we create the shell first, then add the
# ``embedding`` column once we know the dimension.  The HNSW index is
# created separately (see ``_ensure_embedding_index``).

_DDL_EMBEDDINGS_SHELL = """
    CREATE TABLE IF NOT EXISTS cks_object_embeddings (
        object_id  TEXT NOT NULL,
        session_id TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (object_id, session_id)
    )
"""

_CREATE_SESSIONS_MODIFIED_AT_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_sessions_modified_at ON sessions(modified_at)
"""

_CREATE_ARCHIVE_SESSIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS archive_sessions (
        session_id        TEXT PRIMARY KEY,
        data              JSONB NOT NULL,
        latest_version_id TEXT,
        modified_at       TIMESTAMPTZ,
        archived_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_MIGRATE_SESSIONS_MODIFIED_AT = """
    ALTER TABLE sessions ADD COLUMN modified_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""


class PostgresStorage(AsyncRuntimeStorage):
    """
    Persists Runtime sessions, versions, outbox tasks, operation log,
    and vector embeddings in PostgreSQL.

    Construct via the ``connect`` classmethod, not directly --
    opening the pool and running DDL both require awaiting.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        # Cached embedding dimension -- set on first successful insert
        # and never changed afterwards.  None = not yet known.
        self._embed_dim: int | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def connect(
        cls,
        conninfo: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> PostgresStorage:
        """
        Open a connection pool and ensure all tables exist.

        Parameters
        ----------
        conninfo
            psycopg connection string, e.g.
            ``"postgresql://user:pass@host:5432/dbname"``.
        min_size / max_size
            Pool size bounds.  For single-process deployments 1/10 is
            fine; increase ``max_size`` for heavy concurrent workloads.
        """
        pool = AsyncConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            open=False,
        )
        await pool.open()
        storage = cls(pool)
        await storage._create_tables()
        return storage

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._pool.close()

    async def _create_tables(self) -> None:
        async with self._pool.connection() as conn:
            # Core tables
            await conn.execute(_DDL_SESSIONS)
            await conn.execute(_DDL_VERSIONS)
            await conn.execute(_DDL_VERSIONS_IDX)
            # Outbox
            await conn.execute(_DDL_OUTBOX)
            await conn.execute(_DDL_OUTBOX_IDX)
            # Operation log
            await conn.execute(_DDL_OPLOG)
            await conn.execute(_DDL_OPLOG_IDX)
            # Runtime identity (ADR-008)
            await conn.execute(_DDL_IDENTITY)
            # Embedding metadata + shell table
            await conn.execute(_DDL_EMBED_META)
            await conn.execute(_DDL_EMBEDDINGS_SHELL)
            await conn.commit()
            await conn.execute(_CREATE_ARCHIVE_SESSIONS_TABLE)
            await conn.execute(_CREATE_SESSIONS_MODIFIED_AT_INDEX)
            try:
                await conn.execute(_MIGRATE_SESSIONS_MODIFIED_AT)
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
        # Restore cached dimension from DB (survives restarts)
        await self._load_embed_dim()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
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
        new_latest = (
            session.version_history[-1].version_id if session.version_history else None
        )

        async def _write() -> None:
            async with self._pool.connection() as conn:
                if expected_version_id is None:
                    await conn.execute(
                        """
                        INSERT INTO sessions (session_id, data, latest_version_id, modified_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (session_id) DO UPDATE
                        SET data = EXCLUDED.data,
                            latest_version_id = EXCLUDED.latest_version_id,
                            modified_at = now()
                        """,
                        (session.session_id, Jsonb(data), new_latest),
                    )
                    await conn.commit()
                    return

                cur = await conn.execute(
                    """
                    UPDATE sessions
                    SET data = %s, latest_version_id = %s, modified_at = now()
                    WHERE session_id = %s
                      AND latest_version_id IS NOT DISTINCT FROM %s
                    """,
                    (Jsonb(data), new_latest, session.session_id, expected_version_id),
                )
                if cur.rowcount == 0:
                    exists_cur = await conn.execute(
                        "SELECT 1 FROM sessions WHERE session_id = %s",
                        (session.session_id,),
                    )
                    exists = await exists_cur.fetchone()
                    if exists is not None:
                        await conn.rollback()
                        raise ConcurrentModificationError(session.session_id)
                    await conn.execute(
                        """
                        INSERT INTO sessions (session_id, data, latest_version_id, modified_at)
                        VALUES (%s, %s, %s, now())
                        """,
                        (session.session_id, Jsonb(data), new_latest),
                    )
                await conn.commit()

        await _retry_on_transient(_write)

    async def load_session(self, session_id: str) -> RuntimeSession | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT data FROM sessions WHERE session_id = %s", (session_id,)
                )
            ).fetchone()
            if row is None:
                return None
            data = row[0]

            version_rows = await (
                await conn.execute(
                    """
                    SELECT data FROM versions
                    WHERE session_id = %s
                    ORDER BY (data->>'created_at') ASC
                    """,
                    (session_id,),
                )
            ).fetchall()

        session = _session_from_row(data)
        for (vdata,) in version_rows:
            session.add_version(_version_from_row(vdata))
        return session

    async def has_session(self, session_id: str) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = %s", (session_id,)
                )
            ).fetchone()
            return row is not None

    async def list_sessions(self) -> tuple[RuntimeSession, ...]:
        """
        Load all sessions and their version histories in a single
        LEFT JOIN query instead of N+1 separate queries.

        Sessions are assembled in Python: one RuntimeSession per
        ``session_id``, versions appended in ``created_at`` order
        (enforced by the ORDER BY on the joined rows).
        """
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT
                        s.session_id,
                        s.data   AS sdata,
                        v.data   AS vdata
                    FROM sessions s
                    LEFT JOIN versions v USING (session_id)
                    ORDER BY s.session_id,
                             (v.data->>'created_at') ASC NULLS LAST
                    """
                )
            ).fetchall()

        sessions: dict[str, RuntimeSession] = {}
        for session_id, sdata, vdata in rows:
            if session_id not in sessions:
                sessions[session_id] = _session_from_row(sdata)
            if vdata is not None:
                sessions[session_id].add_version(_version_from_row(vdata))

        return tuple(sessions.values())


    async def list_sessions_modified_before(
        self,
        cutoff: datetime,
        limit: int = 1000,
    ) -> list[RuntimeSession]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE modified_at < %s "
                    "ORDER BY modified_at ASC "
                    "LIMIT %s",
                    (cutoff, limit),
                )
            ).fetchall()
        sessions = []
        for (sid,) in rows:
            session = await self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return sessions

    async def list_sessions_modified_since(
        self,
        watermark: datetime,
        limit: int = 1000,
    ) -> list[RuntimeSession]:
        """
        Return sessions whose ``modified_at`` is at or after
        *watermark*, oldest first. Used by ``InferenceStalenessSweeper``
        (ADR-009) -- same indexed ``modified_at`` column
        ``list_sessions_modified_before`` queries, comparison flipped.
        """
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE modified_at >= %s "
                    "ORDER BY modified_at ASC "
                    "LIMIT %s",
                    (watermark, limit),
                )
            ).fetchall()
        sessions = []
        for (sid,) in rows:
            session = await self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return sessions

    async def archive_session(self, session: RuntimeSession) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO archive_sessions
                    (session_id, data, latest_version_id, modified_at, archived_at)
                SELECT session_id, data, latest_version_id, modified_at, now()
                FROM sessions
                WHERE session_id = %s
                ON CONFLICT (session_id) DO UPDATE
                SET archived_at = now()
                """,
                (session.session_id,),
            )
            await conn.execute(
                "DELETE FROM cks_object_embeddings WHERE session_id = %s",
                (session.session_id,),
            )
            await conn.execute(
                "DELETE FROM sessions WHERE session_id = %s",
                (session.session_id,),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def save_version(self, version: RuntimeVersion) -> None:
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

        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO versions (version_id, session_id, data) VALUES (%s, %s, %s)",
                    (version.version_id, version.session_id, Jsonb(data)),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def load_version(self, version_id: str) -> RuntimeVersion | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT data FROM versions WHERE version_id = %s", (version_id,)
                )
            ).fetchone()
        if row is None:
            return None
        return _version_from_row(row[0])

    async def has_version(self, version_id: str) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT 1 FROM versions WHERE version_id = %s", (version_id,)
                )
            ).fetchone()
            return row is not None

    async def list_versions(self) -> tuple[RuntimeVersion, ...]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM versions")).fetchall()
        return tuple(_version_from_row(data) for (data,) in rows)

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    @property
    def supports_outbox(self) -> bool:
        return True

    async def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        """Legacy wrapper -- encodes a projection task and calls enqueue_task."""
        await self.enqueue_task(
            task_type="projection",
            session_id=session_id,
            payload=json.dumps({
                "previous_version_id": previous_version_id,
                "new_version_id": new_version_id,
            }),
        )

    async def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO cks_outbox_tasks
                        (task_type, session_id, payload, status, next_retry_at)
                    VALUES (%s, %s, %s, 'PENDING', now())
                    """,
                    (task_type, session_id, payload),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def dequeue_next_outbox_task(self, task_type: str | None = None) -> OutboxTask | None:
        """
        Atomically claim and return the next eligible task.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` inside a CTE so that
        two workers polling concurrently never both receive the same
        task: the inner SELECT picks the earliest eligible row, FOR
        UPDATE locks it, SKIP LOCKED lets the second worker skip the
        locked row and claim the next one.

        Eligible = PENDING with ``next_retry_at <= now()``, OR
        IN_PROGRESS with a stale ``claimed_at`` (worker crashed/hung).
        ``task_type``, when given, restricts the candidate set to that
        type only -- same rationale as ``SQLiteStorage``'s counterpart.
        """
        async def _claim() -> OutboxTask | None:
            async with self._pool.connection() as conn:
                if task_type is None:
                    row = await (
                        await conn.execute(
                            f"""
                            WITH candidate AS (
                                SELECT task_id FROM cks_outbox_tasks
                                WHERE (status = 'PENDING' AND next_retry_at <= now())
                                   OR (status = 'IN_PROGRESS'
                                       AND claimed_at <= now() - INTERVAL '{_OUTBOX_LEASE_TIMEOUT}')
                                ORDER BY created_at ASC
                                LIMIT 1
                                FOR UPDATE SKIP LOCKED
                            )
                            UPDATE cks_outbox_tasks t
                            SET status = 'IN_PROGRESS', claimed_at = now()
                            FROM candidate
                            WHERE t.task_id = candidate.task_id
                            RETURNING t.task_id, t.task_type, t.session_id,
                                      t.payload, t.retry_count
                            """
                        )
                    ).fetchone()
                else:
                    row = await (
                        await conn.execute(
                            f"""
                            WITH candidate AS (
                                SELECT task_id FROM cks_outbox_tasks
                                WHERE task_type = %s
                                  AND ((status = 'PENDING' AND next_retry_at <= now())
                                   OR (status = 'IN_PROGRESS'
                                       AND claimed_at <= now() - INTERVAL '{_OUTBOX_LEASE_TIMEOUT}'))
                                ORDER BY created_at ASC
                                LIMIT 1
                                FOR UPDATE SKIP LOCKED
                            )
                            UPDATE cks_outbox_tasks t
                            SET status = 'IN_PROGRESS', claimed_at = now()
                            FROM candidate
                            WHERE t.task_id = candidate.task_id
                            RETURNING t.task_id, t.task_type, t.session_id,
                                      t.payload, t.retry_count
                            """,
                            (task_type,),
                        )
                    ).fetchone()
                await conn.commit()
            if row is None:
                return None
            return OutboxTask(
                task_id=row[0],
                task_type=row[1],
                session_id=row[2],
                payload=row[3],
                retry_count=row[4],
            )

        return await _retry_on_transient(_claim)

    async def complete_outbox_task(self, task_id: int) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM cks_outbox_tasks WHERE task_id = %s", (task_id,)
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def fail_outbox_task(
        self, task_id: int, retry_count: int, error: str, next_retry_at: str
    ) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE cks_outbox_tasks
                    SET status        = 'PENDING',
                        retry_count   = %s,
                        next_retry_at = %s::timestamptz,
                        last_error    = %s,
                        claimed_at    = NULL
                    WHERE task_id = %s
                    """,
                    (retry_count, next_retry_at, error, task_id),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def dead_letter_outbox_task(self, task_id: int, error: str) -> None:
        """Permanently mark a task DEAD -- see SQLiteStorage's counterpart."""
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    UPDATE cks_outbox_tasks
                    SET status = 'DEAD',
                        last_error = %s,
                        claimed_at = NULL
                    WHERE task_id = %s
                    """,
                    (error, task_id),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def list_tasks_by_type(
        self,
        task_type: str,
        session_id: str | None = None,
        drain: bool = True,
    ) -> list[OutboxTask]:
        """
        Batch peek/drain read over PENDING tasks of ``task_type``,
        locking the matched rows with ``FOR UPDATE SKIP LOCKED`` so a
        concurrent caller (single-task ``dequeue_next_outbox_task`` or
        another ``list_tasks_by_type`` call) never returns the same row
        twice, then deleting them in the same transaction when
        ``drain`` is true.
        """
        async def _read() -> list[tuple]:
            async with self._pool.connection() as conn:
                if session_id is None:
                    rows = await (
                        await conn.execute(
                            """
                            SELECT task_id, task_type, session_id, payload, retry_count
                            FROM cks_outbox_tasks
                            WHERE task_type = %s AND status = 'PENDING'
                            ORDER BY created_at ASC
                            FOR UPDATE SKIP LOCKED
                            """,
                            (task_type,),
                        )
                    ).fetchall()
                else:
                    rows = await (
                        await conn.execute(
                            """
                            SELECT task_id, task_type, session_id, payload, retry_count
                            FROM cks_outbox_tasks
                            WHERE task_type = %s AND status = 'PENDING' AND session_id = %s
                            ORDER BY created_at ASC
                            FOR UPDATE SKIP LOCKED
                            """,
                            (task_type, session_id),
                        )
                    ).fetchall()
                if drain and rows:
                    task_ids = [row[0] for row in rows]
                    await conn.execute(
                        "DELETE FROM cks_outbox_tasks WHERE task_id = ANY(%s)",
                        (task_ids,),
                    )
                await conn.commit()
                return rows

        rows = await _retry_on_transient(_read)
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

    async def list_dead_letter_tasks(self, task_type: str | None = None) -> list[OutboxTask]:
        """Return every DEAD-lettered task, oldest first. Never drains."""
        async def _read() -> list[tuple]:
            async with self._pool.connection() as conn:
                if task_type is None:
                    rows = await (
                        await conn.execute(
                            """
                            SELECT task_id, task_type, session_id, payload, retry_count
                            FROM cks_outbox_tasks
                            WHERE status = 'DEAD'
                            ORDER BY created_at ASC
                            """
                        )
                    ).fetchall()
                else:
                    rows = await (
                        await conn.execute(
                            """
                            SELECT task_id, task_type, session_id, payload, retry_count
                            FROM cks_outbox_tasks
                            WHERE status = 'DEAD' AND task_type = %s
                            ORDER BY created_at ASC
                            """,
                            (task_type,),
                        )
                    ).fetchall()
                return rows

        rows = await _retry_on_transient(_read)
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

    # ------------------------------------------------------------------
    # Operation log (ADR-007)
    # ------------------------------------------------------------------

    @property
    def supports_operation_log(self) -> bool:
        return True

    async def record_operations(
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

        async def _write() -> None:
            async with self._pool.connection() as conn:
                # psycopg3's AsyncConnection has no executemany -- only
                # AsyncCursor does. Calling conn.executemany(...)
                # directly raises AttributeError on every call, which
                # went unnoticed because the postgres test suite is
                # skipped whenever CKS_TEST_POSTGRES_DSN isn't set.
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO cks_operation_log
                            (session_id, version_id, object_id, op_type, field_key, field_value)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        rows,
                    )
                await conn.commit()

        await _retry_on_transient(_write)

    async def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
        version_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        clauses = ["session_id = %s"]
        params: list[str] = [session_id]
        if object_id is not None:
            clauses.append("object_id = %s")
            params.append(object_id)
        if version_id is not None:
            clauses.append("version_id = %s")
            params.append(version_id)

        query = (
            "SELECT object_id, op_type, field_key, field_value, version_id "
            "FROM cks_operation_log WHERE "
            + " AND ".join(clauses)
            + " ORDER BY op_id"
        )

        async with self._pool.connection() as conn:
            rows = await (await conn.execute(query, tuple(params))).fetchall()

        result = []
        for row in rows:
            raw_val = row[3]
            field_value = json.loads(raw_val) if raw_val is not None else None
            result.append(
                RuntimeFieldOperation(
                    object_id=row[0],
                    op_type=row[1],
                    field_key=row[2],
                    field_value=field_value,
                    version_id=row[4],
                )
            )
        return result

    # ------------------------------------------------------------------
    # Embeddings (pgvector)
    # ------------------------------------------------------------------

    @property
    def supports_embedding_search(self) -> bool:
        return True

    async def save_object_embeddings(
        self, object_id: str, session_id: str, embedding: bytes
    ) -> None:
        """
        Persist a vector embedding for an object.

        On the first call the vector dimension is inferred from
        ``embedding``, the ``cks_object_embeddings.embedding`` column
        is added with that exact ``vector(N)`` type, and the HNSW index
        is created.  Subsequent calls validate that the incoming vector
        matches the stored dimension: a mismatch means the embedding
        model was swapped mid-deployment and is raised as ``ValueError``
        rather than silently polluting the index.
        """
        dim = len(embedding) // 4  # float32 = 4 bytes per component
        await self._ensure_embedding_column(dim)

        vec_literal = _bytes_to_pg_vector(embedding)

        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO cks_object_embeddings
                        (object_id, session_id, embedding, updated_at)
                    VALUES (%s, %s, %s::vector, now())
                    ON CONFLICT (object_id, session_id) DO UPDATE
                    SET embedding  = EXCLUDED.embedding,
                        updated_at = now()
                    """,
                    (object_id, session_id, vec_literal),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM cks_object_embeddings WHERE object_id = %s AND session_id = %s",
                    (object_id, session_id),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def search_embeddings(
        self,
        query_embedding: bytes,
        session_id: str,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """
        Return (object_id, similarity_score) pairs for the ``top_k``
        closest embeddings using the HNSW index.

        The ``<=>`` operator is the cosine *distance* (0 = identical,
        2 = opposite), so similarity = 1 - distance.  Clamped to
        [0.0, 1.0] to match the contract of the SQLite implementation.

        A single SQL ORDER BY lets Postgres use the HNSW index -- no
        in-process numpy matrix multiply over a full table scan.
        """
        if self._embed_dim is None:
            # No embeddings stored yet.
            return []

        vec_literal = _bytes_to_pg_vector(query_embedding)

        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT object_id,
                           GREATEST(0.0, 1.0 - (embedding <=> %s::vector)) AS score
                    FROM cks_object_embeddings
                    WHERE session_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, session_id, vec_literal, top_k),
                )
            ).fetchall()

        return [(row[0], float(row[1])) for row in rows]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute("DELETE FROM cks_operation_log")
                await conn.execute("DELETE FROM cks_object_embeddings")
                await conn.execute("DELETE FROM cks_outbox_tasks")
                await conn.execute("DELETE FROM versions")
                await conn.execute("DELETE FROM sessions")
                await conn.execute("DELETE FROM cks_runtime_identity")
                await conn.commit()

        await _retry_on_transient(_write)

    #
    # ------------------------------------------------------------------
    # Distributed replication (ADR-008)
    # ------------------------------------------------------------------
    #

    async def get_or_create_replica_id(self) -> str | None:
        """
        Return this database's durable replica identity, generating
        and persisting one under the single-row ``cks_runtime_identity``
        table on first call. Mirrors ``SQLiteStorage.get_or_create_replica_id``;
        see there for the single-row/race-safety rationale.
        """

        async def _get_or_create() -> str:
            async with self._pool.connection() as conn:
                row = await (
                    await conn.execute(
                        "SELECT replica_id FROM cks_runtime_identity WHERE id = 1"
                    )
                ).fetchone()
                if row is not None:
                    return str(row[0])

                candidate = str(uuid4())
                try:
                    await conn.execute(
                        "INSERT INTO cks_runtime_identity (id, replica_id) "
                        "VALUES (1, %s)",
                        (candidate,),
                    )
                    await conn.commit()
                    return candidate
                except psycopg.errors.UniqueViolation:
                    await conn.rollback()
                    row = await (
                        await conn.execute(
                            "SELECT replica_id FROM cks_runtime_identity WHERE id = 1"
                        )
                    ).fetchone()
                    assert row is not None
                    return str(row[0])

        return await _retry_on_transient(_get_or_create)

    # ------------------------------------------------------------------
    # Internal helpers — embedding column lifecycle
    # ------------------------------------------------------------------

    async def _load_embed_dim(self) -> None:
        """Restore the cached embedding dimension from the meta table (survives restarts)."""
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT value FROM cks_embedding_meta WHERE key = 'embedding_dim'"
                )
            ).fetchone()
        if row is not None:
            self._embed_dim = int(row[0])

    async def _ensure_embedding_column(self, dim: int) -> None:
        """
        Lazily add the ``vector(dim)`` column and HNSW index on first use.

        On the very first call ``_embed_dim`` is None -- the column
        doesn't exist yet, so we add it and record the dimension.
        Subsequent calls are a fast no-op (``_embed_dim`` is set).

        A dimension mismatch (e.g. swapping the embedding model) raises
        ``ValueError`` immediately so the caller (OutboxEmbeddingWorker)
        can log it instead of silently corrupting the index.
        """
        if self._embed_dim is not None:
            if dim != self._embed_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: stored index uses dim={self._embed_dim}, "
                    f"but the incoming vector has dim={dim}. "
                    "If you intentionally switched embedding models, drop the "
                    "cks_object_embeddings table and restart to re-index."
                )
            return  # Fast path: column already exists and dimension matches.

        # Slow path: first ever embedding.  Add the column + index.
        async with self._pool.connection() as conn:
            # pgvector extension must already be installed by the DBA.
            # We don't CREATE EXTENSION here to avoid requiring superuser
            # privileges -- it only needs to happen once per database.
            await conn.execute(
                f"ALTER TABLE cks_object_embeddings ADD COLUMN IF NOT EXISTS embedding vector({dim})"
            )
            # HNSW index: m=16 (default), ef_construction=64 (default).
            # The index name embeds the dimension so a future model swap
            # can't accidentally reuse a stale index with wrong params.
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_{dim}
                ON cks_object_embeddings
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                """
            )
            await conn.execute(
                "INSERT INTO cks_embedding_meta (key, value) VALUES ('embedding_dim', %s) "
                "ON CONFLICT (key) DO NOTHING",
                (str(dim),),
            )
            await conn.commit()

        self._embed_dim = dim


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _bytes_to_pg_vector(embedding: bytes) -> str:
    """
    Convert a raw float32 byte array into the pgvector text literal
    ``[f1,f2,...]`` that Postgres accepts via a ``%s::vector`` cast.

    pgvector's Python client (``pgvector.psycopg``) can register a
    type codec so the driver handles conversion transparently, but that
    requires ``register_vector`` to be called per-connection, which is
    awkward with a pool. Using a plain text literal avoids the
    per-connection setup at the cost of slightly more network bytes --
    acceptable given that the bottleneck is almost always the HNSW
    distance computation, not serialization.
    """
    n = len(embedding) // 4
    floats = struct.unpack(f"{n}f", embedding)
    return "[" + ",".join(f"{v:.8g}" for v in floats) + "]"


def _session_from_row(data: dict) -> RuntimeSession:
    """Reconstruct a RuntimeSession from a decoded ``sessions.data`` JSONB row."""
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
    return session


def _version_from_row(data: dict) -> RuntimeVersion:
    """Reconstruct a RuntimeVersion from a decoded ``versions.data`` JSONB row."""
    ks = cks.parse(data["knowledge_structure"]) if data.get("knowledge_structure") else None
    patch = deserialize_operators(data["patch"]) if data.get("patch") else None
    return RuntimeVersion(
        session_id=data["session_id"],
        transaction_id=data["transaction_id"],
        knowledge_structure=ks,
        metadata=data["metadata"],
        version_id=data["version_id"],
        created_at=datetime.fromisoformat(data["created_at"]),
        state_hash=data.get("state_hash"),
        patch=patch,
    )