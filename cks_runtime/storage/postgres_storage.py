"""
PostgreSQL-backed Runtime Storage (async).

Implements ``AsyncRuntimeStorage`` for sessions and versions -- the
first slice of the Postgres backend: schema, CRUD, and the
compare-and-swap concurrency guard on ``save_session``. Outbox,
embeddings, and the operation log are deliberately left as the
inherited no-ops from ``AsyncRuntimeStorage`` for now; they need
Postgres-native concurrency primitives of their own (``SELECT ... FOR
UPDATE SKIP LOCKED`` for the outbox, optionally ``pgvector`` for
embeddings) and are follow-up work, not a port of the SQLite tables.

Design choices, and why they differ from ``SQLiteStorage``:

* JSON payloads are stored as ``JSONB``, not ``TEXT``. psycopg
  round-trips ``dict`` <-> ``jsonb`` automatically (writes wrapped in
  ``Jsonb(...)``, reads come back as plain ``dict``), so this also
  drops the manual ``json.dumps``/``json.loads`` pass SQLite needs --
  and it leaves room to index into the payload later (e.g. a GIN
  index) without a schema migration.

* The CAS comparison uses ``IS NOT DISTINCT FROM`` rather than ``=``.
  A session's first-ever commit has ``expected_version_id=None``
  matched against a ``NULL`` column; plain ``=`` never matches NULL
  (in *either* operand) so that first CAS write would always be
  rejected. SQLite's ``IS`` operator already does the NULL-safe thing
  by accident; Postgres needs it spelled out.

* Retry covers ``psycopg.OperationalError`` -- the parent class of
  both ``SerializationFailure`` and ``DeadlockDetected``, Postgres's
  equivalents of SQLite's transient "database is locked". It
  deliberately does *not* cover ``psycopg.IntegrityError`` (e.g.
  ``UniqueViolation`` on a duplicate ``version_id``) or
  ``ConcurrentModificationError`` -- both are legitimate rejections,
  not transient failures, and retrying them blindly would silently
  paper over a real conflict. This mirrors ``_retry_on_locked`` in
  ``sqlite_storage.py`` exactly, just against Postgres's exception
  hierarchy instead of SQLite's message-sniffing.

* Every method borrows a connection from an ``AsyncConnectionPool``
  for the duration of the call rather than holding one open on
  ``self`` -- ``SQLiteStorage`` can get away with a single persistent
  connection because SQLite serializes writers itself; a Postgres
  server expects a pool of short-lived checkouts instead.

Known gap (see ``async_storage.py`` for the full rationale): this
class is not yet wireable into the synchronous ``Runtime`` --
that bridge is intentionally left for a follow-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

import cks
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.async_storage import AsyncRuntimeStorage
from cks_runtime.storage.patch_codec import deserialize_operators, serialize_operators
from cks_runtime.storage.storage import ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion

# Retry tuning for transient Postgres errors under concurrent writers.
# Same shape as sqlite_storage's _WRITE_RETRIES/_WRITE_RETRY_BASE_DELAY_SECONDS
# -- kept as a separate constant pair (not imported from there) because
# the two backends' retryable-error sets are unrelated and tuning one
# should not silently retune the other.
_WRITE_RETRIES = 5
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05


async def _retry_on_transient[T](fn: Callable[[], Awaitable[T]]) -> T:
    """
    Run fn(), retrying with exponential backoff on a transient
    psycopg.OperationalError (covers SerializationFailure,
    DeadlockDetected, and dropped-connection errors). Does NOT retry
    ConcurrentModificationError or psycopg.IntegrityError -- those are
    legitimate rejections (a CAS conflict, a duplicate key), not
    transient contention, and retrying them here would silently hide
    a real conflict from the caller.
    """
    last_exc: BaseException | None = None
    for attempt in range(_WRITE_RETRIES):
        try:
            return await fn()
        except psycopg.OperationalError as exc:
            if attempt >= _WRITE_RETRIES - 1:
                raise
            last_exc = exc
            await asyncio.sleep(_WRITE_RETRY_BASE_DELAY_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


_CREATE_SESSIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id        TEXT PRIMARY KEY,
        data              JSONB NOT NULL,
        latest_version_id TEXT
    )
"""

_CREATE_VERSIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS versions (
        version_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        data       JSONB NOT NULL
    )
"""

_CREATE_VERSIONS_SESSION_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_versions_session ON versions(session_id)
"""


class PostgresStorage(AsyncRuntimeStorage):
    """Persists Runtime sessions and versions in PostgreSQL using JSONB."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @classmethod
    async def connect(
        cls,
        conninfo: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> PostgresStorage:
        """
        Open a connection pool against `conninfo` and ensure the
        schema exists. This is a classmethod factory rather than
        async work in ``__init__`` because opening a pool and running
        DDL both require awaiting -- ``__init__`` cannot await.
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
            await conn.execute(_CREATE_SESSIONS_TABLE)
            await conn.execute(_CREATE_VERSIONS_TABLE)
            await conn.execute(_CREATE_VERSIONS_SESSION_INDEX)
            await conn.commit()

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
            "diagnostics": [],  # diagnostics are not persisted by default
            "version_history_ids": [v.version_id for v in session.version_history],
            "parent_session_id": session.parent_session_id,
            "parent_version_id": session.parent_version_id,
            "closed": session.closed,
        }
        new_latest_version_id = (
            session.version_history[-1].version_id if session.version_history else None
        )

        async def _write() -> None:
            async with self._pool.connection() as conn:
                if expected_version_id is None:
                    # No CAS requested (initial create, rollback, abort):
                    # unconditional upsert.
                    await conn.execute(
                        """
                        INSERT INTO sessions (session_id, data, latest_version_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (session_id) DO UPDATE
                        SET data = EXCLUDED.data,
                            latest_version_id = EXCLUDED.latest_version_id
                        """,
                        (session.session_id, Jsonb(data), new_latest_version_id),
                    )
                    await conn.commit()
                    return

                cur = await conn.execute(
                    """
                    UPDATE sessions SET data = %s, latest_version_id = %s
                    WHERE session_id = %s
                      AND latest_version_id IS NOT DISTINCT FROM %s
                    """,
                    (
                        Jsonb(data),
                        new_latest_version_id,
                        session.session_id,
                        expected_version_id,
                    ),
                )
                if cur.rowcount == 0:
                    # Either the session doesn't exist yet, or another
                    # writer already advanced it past expected_version_id.
                    # Distinguish the two so a first-ever commit on a
                    # session that was created without going through this
                    # CAS path (expected_version_id=None) isn't rejected.
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
                        INSERT INTO sessions (session_id, data, latest_version_id)
                        VALUES (%s, %s, %s)
                        """,
                        (session.session_id, Jsonb(data), new_latest_version_id),
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

            version_rows = await (
                await conn.execute(
                    "SELECT data FROM versions WHERE session_id = %s", (session_id,)
                )
            ).fetchall()

        versions: list[RuntimeVersion] = []
        for (vdata,) in version_rows:
            versions.append(_version_from_row(vdata))

        # Sort chronologically and add to session, same as SQLiteStorage.
        versions.sort(key=lambda v: v.created_at)
        for version in versions:
            session.add_version(version)

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
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute("SELECT session_id FROM sessions")
            ).fetchall()
        sessions = []
        for (sid,) in rows:
            session = await self.load_session(sid)
            if session is not None:
                sessions.append(session)
        return tuple(sessions)

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
                # Strict INSERT, not upsert: version_id is a fresh uuid4
                # per version, so a collision means two writers generated
                # a version under the same id -- a bug worth surfacing as
                # UniqueViolation, not silently overwriting one writer's
                # version with another's. Not caught by _retry_on_transient
                # (IntegrityError, not OperationalError), so it propagates
                # immediately -- same behaviour as SQLiteStorage's plain
                # INSERT.
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
    # Maintenance
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute("DELETE FROM versions")
                await conn.execute("DELETE FROM sessions")
                await conn.commit()

        await _retry_on_transient(_write)


def _version_from_row(data: dict) -> RuntimeVersion:
    """Reconstruct a RuntimeVersion from a decoded `versions.data` JSONB row."""
    ks = cks.parse(data["knowledge_structure"]) if data["knowledge_structure"] else None
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
