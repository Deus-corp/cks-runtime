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
import base64
import json
import struct
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import cks
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.async_storage import AsyncRuntimeStorage
from cks_runtime.storage.patch_codec import (
    _thaw,
    deserialize_operators,
    serialize_operators,
)
from cks_runtime.storage.storage import (
    AgentLivenessRecord,
    ConcurrentModificationError,
    OutboxTask,
)
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

# ---------------------------------------------------------------------------
# DDL — graph registry (Memory Agent v1)
# ---------------------------------------------------------------------------

_DDL_GRAPH_REGISTRY = """
    CREATE TABLE IF NOT EXISTS graph_registry (
        name        TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL,
        description TEXT,
        tags        TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

# `public` for the gallery (Memory Agent v2). Defaults to false so every
# pre-existing registered graph stays private, preserving backward
# compatibility -- same rationale as SQLiteStorage's migration.
_MIGRATE_GRAPH_REGISTRY_PUBLIC = """
    ALTER TABLE graph_registry ADD COLUMN public BOOLEAN NOT NULL DEFAULT false
"""

# `source_graph_name` (clone lineage) -- same rationale as SQLiteStorage's
# migration: NULL by default so pre-existing rows are treated as having
# no known lineage, preserving backward compatibility.
_MIGRATE_GRAPH_REGISTRY_SOURCE_GRAPH_NAME = """
    ALTER TABLE graph_registry ADD COLUMN source_graph_name TEXT
"""

# `visibility` / `team` (Memory Agent v3 -- library/teams) -- same
# rationale and three-way scope as SQLiteStorage's migration. Existing
# rows are backfilled from their current `public` value.
_MIGRATE_GRAPH_REGISTRY_VISIBILITY = """
    ALTER TABLE graph_registry ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'
"""

_MIGRATE_GRAPH_REGISTRY_VISIBILITY_BACKFILL = """
    UPDATE graph_registry SET visibility = 'public' WHERE public = true
"""

_MIGRATE_GRAPH_REGISTRY_TEAM = """
    ALTER TABLE graph_registry ADD COLUMN team TEXT
"""

# `lifecycle_state` (Graph Lifecycle -- first slice) -- same rationale
# and migration pattern as SQLiteStorage. Existing rows are backfilled
# to 'published' when already public, otherwise 'draft'.
_MIGRATE_GRAPH_REGISTRY_LIFECYCLE_STATE = """
    ALTER TABLE graph_registry ADD COLUMN lifecycle_state TEXT
"""

_MIGRATE_GRAPH_REGISTRY_LIFECYCLE_STATE_BACKFILL = """
    UPDATE graph_registry SET lifecycle_state =
        CASE WHEN visibility = 'public' THEN 'published' ELSE 'draft' END
    WHERE lifecycle_state IS NULL
"""

# ---------------------------------------------------------------------------
# DDL — standalone agent liveness (ADR-014). One row per process
# instance (not per process_kind) -- see SQLiteStorage's schema comment
# for the full rationale, identical here.
# ---------------------------------------------------------------------------

_DDL_AGENT_LIVENESS = """
    CREATE TABLE IF NOT EXISTS cks_agent_liveness (
        instance_id         TEXT PRIMARY KEY,
        process_kind        TEXT NOT NULL,
        hostname            TEXT NOT NULL,
        pid                 INTEGER NOT NULL,
        liveness_interval_s DOUBLE PRECISION NOT NULL,
        started_at          TIMESTAMPTZ NOT NULL,
        last_heartbeat_at   TIMESTAMPTZ NOT NULL,
        current_task_id     INTEGER,
        current_task_type   TEXT
    )
"""

_DDL_AGENT_LIVENESS_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_agent_liveness_kind
    ON cks_agent_liveness(process_kind, last_heartbeat_at)
"""

# ADR-016 §1: NULL/'running' = no stop requested (default);
# 'stop_requested' = pending. Same ALTER-TABLE-if-missing convention
# as _MIGRATE_GRAPH_REGISTRY_PUBLIC below.
_MIGRATE_AGENT_LIVENESS_DESIRED_STATE = """
    ALTER TABLE cks_agent_liveness ADD COLUMN desired_state TEXT
"""

# ---------------------------------------------------------------------------
# DDL — sweeper control (ADR-015 §1). One row only for sweepers that
# have ever had their default overridden -- see SQLiteStorage's schema
# comment for the full rationale, identical here.
# ---------------------------------------------------------------------------

_DDL_SWEEPER_CONTROL = """
    CREATE TABLE IF NOT EXISTS cks_sweeper_control (
        agent_id        TEXT PRIMARY KEY,
        desired_running BOOLEAN NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL
    )
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
            # Graph registry (Memory Agent v1)
            await conn.execute(_DDL_GRAPH_REGISTRY)
            await conn.commit()
            try:
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_PUBLIC)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            try:
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_SOURCE_GRAPH_NAME)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            try:
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_VISIBILITY)
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_VISIBILITY_BACKFILL)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            try:
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_TEAM)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            try:
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_LIFECYCLE_STATE)
                await conn.execute(_MIGRATE_GRAPH_REGISTRY_LIFECYCLE_STATE_BACKFILL)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            # Standalone agent liveness (ADR-014)
            await conn.execute(_DDL_AGENT_LIVENESS)
            await conn.execute(_DDL_AGENT_LIVENESS_INDEX)
            await conn.commit()
            try:
                await conn.execute(_MIGRATE_AGENT_LIVENESS_DESIRED_STATE)
                await conn.commit()
            except psycopg.errors.DuplicateColumn:
                await conn.rollback()
            # Sweeper control (ADR-015)
            await conn.execute(_DDL_SWEEPER_CONTROL)
            await conn.commit()
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

    async def touch_outbox_task(self, task_id: int) -> bool:
        """Renew an IN_PROGRESS task's lease -- see SQLiteStorage's counterpart."""
        async def _write() -> bool:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    """
                    UPDATE cks_outbox_tasks
                    SET claimed_at = now()
                    WHERE task_id = %s AND status = 'IN_PROGRESS'
                    """,
                    (task_id,),
                )
                await conn.commit()
                return cur.rowcount > 0

        return await _retry_on_transient(_write)

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

    async def list_dead_letter_tasks(
        self, task_type: str | None = None, session_id: str | None = None
    ) -> list[OutboxTask]:
        """Return every DEAD-lettered task, oldest first. Never drains."""
        async def _read() -> list[tuple]:
            clauses = ["status = 'DEAD'"]
            params: list[str] = []
            if task_type is not None:
                clauses.append("task_type = %s")
                params.append(task_type)
            if session_id is not None:
                clauses.append("session_id = %s")
                params.append(session_id)
            query = f"""
                SELECT task_id, task_type, session_id, payload, retry_count, last_error
                FROM cks_outbox_tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC
                """
            async with self._pool.connection() as conn:
                rows = await (await conn.execute(query, tuple(params))).fetchall()
                return rows

        rows = await _retry_on_transient(_read)
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
                json.dumps(_thaw(op.field_value)) if op.op_type == "set_field" else None,
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

    # ------------------------------------------------------------------
    # Graph registry (Memory Agent v1)
    # ------------------------------------------------------------------

    _GRAPH_COLUMNS = (
        "name, session_id, description, tags, created_at, updated_at, "
        "public, source_graph_name, visibility, team, lifecycle_state"
    )

    @staticmethod
    def _graph_row_to_dict(row: tuple) -> dict:
        visibility = row[8] if len(row) > 8 and row[8] else ("public" if row[6] else "private")
        lifecycle_state = row[10] if len(row) > 10 and row[10] else (
            "published" if visibility == "public" else "draft"
        )
        return {
            "name": row[0],
            "session_id": row[1],
            "description": row[2] or "",
            "tags": row[3] or "",
            "created_at": row[4],
            "updated_at": row[5],
            "public": bool(row[6]),
            "source_graph_name": row[7] if len(row) > 7 else None,
            "visibility": visibility,
            "team": row[9] if len(row) > 9 else None,
            "lifecycle_state": lifecycle_state,
        }

    async def register_graph(
        self,
        name: str,
        session_id: str,
        description: str = "",
        tags: str = "",
        public: bool = False,
        source_graph_name: str | None = None,
        visibility: str | None = None,
        team: str | None = None,
        lifecycle_state: str | None = None,
    ) -> None:
        resolved_visibility = visibility or ("public" if public else "private")
        resolved_public = resolved_visibility == "public"

        async def _write() -> None:
            # COALESCE: a plain re-register (source_graph_name=None) must
            # not wipe out lineage recorded by an earlier
            # clone_graph(copy_name=...) call for this same name -- same
            # rationale as SQLiteStorage. `lifecycle_state` follows the
            # same COALESCE rule so a plain re-register doesn't undo a
            # transition made via update_graph_lifecycle; when NULL on
            # a brand-new row, _graph_row_to_dict computes the
            # 'published'-if-public-else-'draft' default on read.
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO graph_registry (name, session_id, description, tags, public, source_graph_name, visibility, team, lifecycle_state, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (name) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        description = EXCLUDED.description,
                        tags = EXCLUDED.tags,
                        public = EXCLUDED.public,
                        source_graph_name = COALESCE(EXCLUDED.source_graph_name, graph_registry.source_graph_name),
                        visibility = EXCLUDED.visibility,
                        team = EXCLUDED.team,
                        lifecycle_state = COALESCE(EXCLUDED.lifecycle_state, graph_registry.lifecycle_state),
                        updated_at = now()
                    """,
                    (
                        name,
                        session_id,
                        description,
                        tags,
                        resolved_public,
                        source_graph_name,
                        resolved_visibility,
                        team,
                        lifecycle_state,
                    ),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def get_graph(self, name: str) -> dict | None:
        async def _read() -> tuple | None:
            async with self._pool.connection() as conn:
                return await (
                    await conn.execute(
                        f"SELECT {self._GRAPH_COLUMNS} FROM graph_registry WHERE name = %s",
                        (name,),
                    )
                ).fetchone()

        row = await _retry_on_transient(_read)
        if row is None:
            return None
        return self._graph_row_to_dict(row)

    async def unregister_graph(self, name: str) -> bool:
        """Remove a registered graph entry by name.

        Returns True if a row existed and was deleted, False otherwise.
        Only removes the registry mapping -- the underlying session and
        its Knowledge Structure are left untouched.
        """

        async def _write() -> bool:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    "DELETE FROM graph_registry WHERE name = %s",
                    (name,),
                )
                await conn.commit()
                return cursor.rowcount > 0

        return await _retry_on_transient(_write)

    async def list_graphs(
        self,
        tag: str | None = None,
        public_only: bool = False,
        team: str | None = None,
    ) -> list[dict]:
        async def _read() -> list[tuple]:
            select = f"SELECT {self._GRAPH_COLUMNS} FROM graph_registry"
            clauses: list[str] = []
            params: list[object] = []
            if tag is not None:
                clauses.append("tags LIKE %s")
                params.append(f"%{tag}%")
            if public_only:
                clauses.append("visibility = 'public'")
            elif team:
                clauses.append(
                    "(visibility = 'public' OR (visibility = 'team' AND team = %s))"
                )
                params.append(team)
            if clauses:
                select += " WHERE " + " AND ".join(clauses)
            select += " ORDER BY updated_at DESC"
            async with self._pool.connection() as conn:
                return await (await conn.execute(select, params)).fetchall()

        rows = await _retry_on_transient(_read)
        return [self._graph_row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Backup / restore (ADR-012)
    # ------------------------------------------------------------------

    async def export_storage(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary containing every
        session, version, graph, embedding, and outbox task in this
        database, for backup or migration to another backend.
        """
        async with self._pool.connection() as conn:
            # Sessions
            session_rows = await (
                await conn.execute(
                    "SELECT session_id, data, latest_version_id, modified_at FROM sessions"
                )
            ).fetchall()
            sessions = [
                {
                    "session_id": row[0],
                    "data": row[1],
                    "latest_version_id": row[2],
                    "modified_at": row[3].isoformat() if row[3] else None,
                }
                for row in session_rows
            ]

            # Versions
            version_rows = await (
                await conn.execute("SELECT version_id, session_id, data FROM versions")
            ).fetchall()
            versions = [
                {
                    "version_id": row[0],
                    "session_id": row[1],
                    "data": row[2],
                }
                for row in version_rows
            ]

            # Graph registry
            graph_rows = await (
                await conn.execute(
                    f"SELECT {self._GRAPH_COLUMNS} FROM graph_registry"
                )
            ).fetchall()
            graphs = [self._graph_row_to_dict(row) for row in graph_rows]

            # Embeddings (base64-encoded, same as SQLiteStorage)
            emb_rows = await (
                await conn.execute(
                    "SELECT object_id, session_id, embedding, updated_at "
                    "FROM cks_object_embeddings"
                )
            ).fetchall()
            embeddings = [
                {
                    "object_id": row[0],
                    "session_id": row[1],
                    "embedding_b64": base64.b64encode(row[2]).decode("ascii") if row[2] else None,
                    "updated_at": row[3].isoformat() if row[3] else None,
                }
                for row in emb_rows
            ]

            # Outbox (PENDING/FAILED only, same as SQLiteStorage)
            outbox_rows = await (
                await conn.execute(
                    "SELECT task_type, session_id, payload, status, retry_count "
                    "FROM cks_outbox_tasks WHERE status IN ('PENDING', 'FAILED')"
                )
            ).fetchall()
            outbox = [
                {
                    "task_type": row[0],
                    "session_id": row[1],
                    "payload": row[2],
                    "status": row[3],
                    "retry_count": row[4],
                }
                for row in outbox_rows
            ]

        return {
            "sessions": sessions,
            "versions": versions,
            "graphs": graphs,
            "embeddings": embeddings,
            "outbox": outbox,
        }

    async def import_storage(self, data: dict[str, Any], mode: str = "merge") -> None:
        """Import data previously exported via ``export_storage`` into
        this Postgres backend.

        ``mode`` is ``"clear"`` (delete all existing rows first) or
        ``"merge"`` (add/update alongside existing data).
        """
        async with self._pool.connection() as conn:
            if mode == "clear":
                await conn.execute("DELETE FROM cks_outbox_tasks")
                await conn.execute("DELETE FROM cks_object_embeddings")
                await conn.execute("DELETE FROM graph_registry")
                await conn.execute("DELETE FROM versions")
                await conn.execute("DELETE FROM sessions")

            # Sessions
            for s in data.get("sessions") or []:
                await conn.execute(
                    """
                    INSERT INTO sessions (session_id, data, latest_version_id, modified_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        data = EXCLUDED.data,
                        latest_version_id = EXCLUDED.latest_version_id,
                        modified_at = EXCLUDED.modified_at
                    """,
                    (
                        s["session_id"],
                        Jsonb(s["data"]),
                        s.get("latest_version_id"),
                        s.get("modified_at"),
                    ),
                )

            # Versions
            for v in data.get("versions") or []:
                await conn.execute(
                    """
                    INSERT INTO versions (version_id, session_id, data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (version_id) DO UPDATE SET
                        data = EXCLUDED.data
                    """,
                    (v["version_id"], v["session_id"], Jsonb(v["data"])),
                )

            # Graph registry
            for g in data.get("graphs") or []:
                resolved_visibility = g.get("visibility") or (
                    "public" if g.get("public") else "private"
                )
                await conn.execute(
                    """
                    INSERT INTO graph_registry (name, session_id, description, tags, public, source_graph_name, visibility, team, lifecycle_state, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        description = EXCLUDED.description,
                        tags = EXCLUDED.tags,
                        public = EXCLUDED.public,
                        source_graph_name = EXCLUDED.source_graph_name,
                        visibility = EXCLUDED.visibility,
                        team = EXCLUDED.team,
                        lifecycle_state = EXCLUDED.lifecycle_state,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        g["name"],
                        g["session_id"],
                        g.get("description", ""),
                        g.get("tags", ""),
                        g.get("public", False),
                        g.get("source_graph_name"),
                        resolved_visibility,
                        g.get("team"),
                        g.get("lifecycle_state")
                        or ("published" if resolved_visibility == "public" else "draft"),
                        g.get("updated_at", datetime.now(UTC).isoformat()),
                    ),
                )

            # Embeddings
            for e in data.get("embeddings") or []:
                emb_bytes = base64.b64decode(e["embedding_b64"]) if e.get("embedding_b64") else None
                if emb_bytes is None:
                    continue
                # We need the vector dimension to be set up first;
                # ensure_embedding_column will create the column + index
                # if this is the first embedding.
                dim = len(emb_bytes) // 4
                await self._ensure_embedding_column(dim)
                vec_literal = _bytes_to_pg_vector(emb_bytes)
                await conn.execute(
                    """
                    INSERT INTO cks_object_embeddings (object_id, session_id, embedding, updated_at)
                    VALUES (%s, %s, %s::vector, %s)
                    ON CONFLICT (object_id, session_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (e["object_id"], e["session_id"], vec_literal, e.get("updated_at", datetime.now(UTC).isoformat())),
                )

            # Outbox
            for o in data.get("outbox") or []:
                await conn.execute(
                    """
                    INSERT INTO cks_outbox_tasks (task_type, session_id, payload, status, retry_count)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (o["task_type"], o["session_id"], o["payload"], o["status"], o.get("retry_count", 0)),
                )

            await conn.commit()

    # ------------------------------------------------------------------
    # Standalone agent liveness (ADR-014)
    # ------------------------------------------------------------------

    @property
    def supports_agent_liveness(self) -> bool:
        return True

    async def upsert_agent_liveness(self, record: AgentLivenessRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO cks_agent_liveness
                    (instance_id, process_kind, hostname, pid,
                     liveness_interval_s, started_at, last_heartbeat_at,
                     current_task_id, current_task_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instance_id) DO UPDATE SET
                    last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                    current_task_id = EXCLUDED.current_task_id,
                    current_task_type = EXCLUDED.current_task_type
                """,
                (
                    record.instance_id,
                    record.process_kind,
                    record.hostname,
                    record.pid,
                    record.liveness_interval_s,
                    record.started_at,
                    record.last_heartbeat_at,
                    record.current_task_id,
                    record.current_task_type,
                ),
            )
            await conn.commit()

    async def prune_agent_liveness(self, older_than_seconds: float) -> int:
        async def _write() -> int:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    """
                    DELETE FROM cks_agent_liveness
                    WHERE last_heartbeat_at < (now() - %s * INTERVAL '1 second')
                    """,
                    (older_than_seconds,),
                )
                await conn.commit()
                return cur.rowcount if cur.rowcount is not None else 0

        return await _retry_on_transient(_write)

    async def list_agent_liveness(self) -> list[AgentLivenessRecord]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT instance_id, process_kind, hostname, pid,
                           liveness_interval_s, started_at, last_heartbeat_at,
                           current_task_id, current_task_type, desired_state
                    FROM cks_agent_liveness
                    ORDER BY started_at DESC
                    """
                )
            ).fetchall()
        return [
            AgentLivenessRecord(
                instance_id=row[0],
                process_kind=row[1],
                hostname=row[2],
                pid=row[3],
                liveness_interval_s=row[4],
                started_at=row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
                last_heartbeat_at=row[6].isoformat() if hasattr(row[6], "isoformat") else row[6],
                current_task_id=row[7],
                current_task_type=row[8],
                desired_state=row[9],
            )
            for row in rows
        ]

    async def get_agent_liveness(self, instance_id: str) -> AgentLivenessRecord | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT instance_id, process_kind, hostname, pid,
                           liveness_interval_s, started_at, last_heartbeat_at,
                           current_task_id, current_task_type, desired_state
                    FROM cks_agent_liveness
                    WHERE instance_id = %s
                    """,
                    (instance_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return AgentLivenessRecord(
            instance_id=row[0],
            process_kind=row[1],
            hostname=row[2],
            pid=row[3],
            liveness_interval_s=row[4],
            started_at=row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
            last_heartbeat_at=row[6].isoformat() if hasattr(row[6], "isoformat") else row[6],
            current_task_id=row[7],
            current_task_type=row[8],
            desired_state=row[9],
        )

    async def request_agent_stop(self, instance_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE cks_agent_liveness
                SET desired_state = 'stop_requested'
                WHERE instance_id = %s
                """,
                (instance_id,),
            )
            await conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Sweeper control (ADR-015)
    # ------------------------------------------------------------------

    async def set_sweeper_desired_running(self, agent_id: str, desired_running: bool) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO cks_sweeper_control (agent_id, desired_running, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (agent_id) DO UPDATE SET
                    desired_running = EXCLUDED.desired_running,
                    updated_at = EXCLUDED.updated_at
                """,
                (agent_id, desired_running),
            )
            await conn.commit()

    async def get_sweeper_desired_running(self, agent_id: str) -> bool | None:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT desired_running FROM cks_sweeper_control WHERE agent_id = %s",
                    (agent_id,),
                )
            ).fetchone()
        return bool(row[0]) if row is not None else None


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
    patch = deserialize_operators(data["patch"]) if data.get("patch") is not None else None
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