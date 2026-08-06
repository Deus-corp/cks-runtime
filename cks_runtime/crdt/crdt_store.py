"""
CRDTStore -- a grow-only set (G-Set) of KnowledgeObjects, gossip-
mergeable across nodes with no conflict resolution needed (ADR-013,
Stage 1: only add-wins is implemented; no removal, no MV-Register,
no LWW -- see ADR-013's "Explicitly out of scope" section).

Object identity
----------------
An object's ``id`` in this store is the hex-encoded SHA-256 leaf hash
of its ``cks.KnowledgeObject`` (identity + structure), not its
application-level ``ObjectIdentity.id``. Two nodes that independently
produce bit-identical KnowledgeObjects converge on the same CRDT
record automatically; this is what makes "add if not already present"
a correct, order-independent G-Set merge -- there is no way for two
different objects to collide into one record, and no way for the same
logical write replayed twice (e.g. via two different gossip peers) to
be double-counted.

Three backends are provided, mirroring the existing storage layer's
split:

- ``SQLiteCRDTStore`` (sync, `_retry_on_locked`-style retry)
- ``PostgresCRDTStore`` (async, `_retry_on_transient`-style retry)
- ``InMemoryCRDTStore`` (sync, for tests)

All three share the same public method surface so ``GossipAdapter``
(or tests) can be written against either without caring which is
plugged in, aside from sync vs. async.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cks_runtime.crdt.merkle_tree import (
    DDL_STATEMENTS,
    PostgresMerkleTree,
    SQLiteMerkleTree,
)
from cks_runtime.crdt.version_vector import VersionVector

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

# ---------------------------------------------------------------------------
# Retry helpers (same tuning/shape as sqlite_storage.py / postgres_storage.py;
# duplicated rather than imported since those two are private module-level
# helpers not part of either module's public surface, and this module must
# not depend on either storage backend module at all -- see the "do not
# touch SQLiteStorage/PostgresStorage" constraint in ADR-013).
# ---------------------------------------------------------------------------

_WRITE_RETRIES = 5
_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05


def _retry_on_locked[T](fn: Callable[[], T]) -> T:
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


async def _retry_on_transient(fn: Callable[[], Awaitable[Any]]) -> Any:
    import asyncio

    import psycopg

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


def object_id_for(knowledge_object: Any) -> str:
    """
    Compute the CRDT record id for a ``cks.KnowledgeObject`` -- its
    hex-encoded SHA-256 leaf hash. Falls back to hashing a dict
    payload's ``id``/``identity`` field for callers that pass a plain
    dict (e.g. reconstructed gossip payloads) rather than a live
    ``cks.KnowledgeObject`` instance.
    """
    leaf_hash = getattr(knowledge_object, "_hash", None)
    if isinstance(leaf_hash, bytes):
        return leaf_hash.hex()
    if isinstance(knowledge_object, dict) and isinstance(knowledge_object.get("id"), str):
        return knowledge_object["id"]
    raise TypeError(
        "object_id_for expects a cks.KnowledgeObject or a dict with a "
        f"precomputed 'id', got {type(knowledge_object)!r}"
    )


def _serialize_object(knowledge_object: Any, object_id: str) -> dict[str, Any]:
    """Build the JSON-serialisable record stored in ``data`` for one object."""
    if isinstance(knowledge_object, dict):
        return dict(knowledge_object)
    identity = knowledge_object.identity
    return {
        "id": object_id,
        "identity": {
            "id": identity.id,
            "type": identity.type,
            "name": identity.name,
        },
        "structure": dict(knowledge_object.structure),
    }


def _object_type(record: dict[str, Any]) -> str:
    identity = record.get("identity") or {}
    return str(identity.get("type", record.get("type", "unknown")))


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


class SQLiteCRDTStore:
    """G-Set of KnowledgeObjects persisted in SQLite, with an incrementally-maintained Merkle tree."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._create_tables()
        self.merkle = SQLiteMerkleTree(conn, _retry_on_locked)

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_knowledge_objects (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_crdt_state (
                node_id        TEXT PRIMARY KEY,
                version_vector TEXT NOT NULL,
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.commit()

    # -- G-Set -------------------------------------------------------------

    def add_object(self, knowledge_object: Any) -> bool:
        """
        Add ``knowledge_object`` if its id is not already present.
        Returns True iff it was new (i.e. this call actually inserted
        a row and updated the Merkle path); False if it was already
        known, in which case the tree is untouched.
        """
        object_id = object_id_for(knowledge_object)
        record = _serialize_object(knowledge_object, object_id)

        def _write() -> bool:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO cks_knowledge_objects (id, type, data, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    object_id,
                    _object_type(record),
                    json.dumps(record, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

        was_new = _retry_on_locked(_write)
        if was_new:
            self.merkle.update_merkle_path(object_id)
        return was_new

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT data FROM cks_knowledge_objects WHERE id = ?", (object_id,)
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row is not None else None

    def list_objects(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT data FROM cks_knowledge_objects ORDER BY created_at"
        )
        return [json.loads(row[0]) for row in cur.fetchall()]

    def merge_objects(self, objects: list[Any]) -> int:
        """Add every object in ``objects``; return how many were new."""
        return sum(1 for obj in objects if self.add_object(obj))

    # -- Merkle delegation ---------------------------------------------------

    def get_root_hash(self) -> str:
        return self.merkle.get_root_hash()

    def get_children_hashes(self, prefix: str) -> list[str]:
        return self.merkle.get_children_hashes(prefix)

    # -- Version vector ------------------------------------------------------

    def update_version_vector(self, node_id: str, vv: VersionVector) -> None:
        def _write() -> None:
            self._conn.execute(
                """
                INSERT INTO cks_crdt_state (node_id, version_vector, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    version_vector = excluded.version_vector,
                    updated_at = excluded.updated_at
                """,
                (node_id, json.dumps(vv.to_dict()), datetime.now(UTC).isoformat()),
            )
            self._conn.commit()

        _retry_on_locked(_write)

    def get_version_vector(self, node_id: str) -> VersionVector:
        cur = self._conn.execute(
            "SELECT version_vector FROM cks_crdt_state WHERE node_id = ?", (node_id,)
        )
        row = cur.fetchone()
        if row is None:
            return VersionVector()
        return VersionVector.from_dict(json.loads(row[0]))


# ---------------------------------------------------------------------------
# PostgreSQL backend (async)
# ---------------------------------------------------------------------------


class PostgresCRDTStore:
    """G-Set of KnowledgeObjects persisted in PostgreSQL, tree maintained by trigger + Python fallback."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self.merkle = PostgresMerkleTree(pool, _retry_on_transient)

    async def ensure_schema(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(_DDL_KNOWLEDGE_OBJECTS)
            await conn.execute(_DDL_CRDT_STATE)
            for statement in DDL_STATEMENTS:
                await conn.execute(statement)

    async def add_object(self, knowledge_object: Any) -> bool:
        object_id = object_id_for(knowledge_object)
        record = _serialize_object(knowledge_object, object_id)

        async def _write() -> bool:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO cks_knowledge_objects (id, type, data, created_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (object_id, _object_type(record), json.dumps(record, sort_keys=True)),
                )
                await conn.commit()
                return cur.rowcount > 0

        was_new = await _retry_on_transient(_write)
        if was_new:
            # The DB trigger (see merkle_tree.DDL_MERKLE_TRIGGER)
            # already updated the path as part of the INSERT above;
            # this is a defensive no-op re-derivation for
            # deployments that created the table without the
            # trigger installed (e.g. via ensure_schema alone with a
            # pgcrypto-less database). Cheap relative to the insert,
            # and idempotent.
            await self.merkle.update_merkle_path(object_id)
        return was_new

    async def get_object(self, object_id: str) -> dict[str, Any] | None:
        async def _read() -> dict[str, Any] | None:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT data FROM cks_knowledge_objects WHERE id = %s", (object_id,)
                )
                row = await cur.fetchone()
                return row[0] if row is not None else None

        return await _retry_on_transient(_read)

    async def list_objects(self) -> list[dict[str, Any]]:
        async def _read() -> list[dict[str, Any]]:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT data FROM cks_knowledge_objects ORDER BY created_at"
                )
                return [row[0] async for row in cur]

        return await _retry_on_transient(_read)

    async def merge_objects(self, objects: list[Any]) -> int:
        count = 0
        for obj in objects:
            if await self.add_object(obj):
                count += 1
        return count

    async def get_root_hash(self) -> str:
        return await self.merkle.get_root_hash()

    async def get_children_hashes(self, prefix: str) -> list[str]:
        return await self.merkle.get_children_hashes(prefix)

    async def update_version_vector(self, node_id: str, vv: VersionVector) -> None:
        async def _write() -> None:
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO cks_crdt_state (node_id, version_vector, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (node_id) DO UPDATE SET
                        version_vector = EXCLUDED.version_vector,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (node_id, json.dumps(vv.to_dict())),
                )
                await conn.commit()

        await _retry_on_transient(_write)

    async def get_version_vector(self, node_id: str) -> VersionVector:
        async def _read() -> VersionVector:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT version_vector FROM cks_crdt_state WHERE node_id = %s",
                    (node_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return VersionVector()
                return VersionVector.from_dict(row[0])

        return await _retry_on_transient(_read)


_DDL_KNOWLEDGE_OBJECTS = """
    CREATE TABLE IF NOT EXISTS cks_knowledge_objects (
        id         VARCHAR(64) PRIMARY KEY,
        type       TEXT NOT NULL,
        data       JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_DDL_CRDT_STATE = """
    CREATE TABLE IF NOT EXISTS cks_crdt_state (
        node_id        TEXT PRIMARY KEY,
        version_vector JSONB NOT NULL,
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


# ---------------------------------------------------------------------------
# In-memory backend (tests)
# ---------------------------------------------------------------------------


class InMemoryCRDTStore:
    """
    G-Set of KnowledgeObjects held in process memory, with a
    from-scratch-rebuilt-on-read Merkle tree (no incremental
    maintenance -- fine for the small object counts unit tests use;
    not intended for production).
    """

    def __init__(self) -> None:
        self._objects: dict[str, dict[str, Any]] = {}
        self._vectors: dict[str, VersionVector] = {}
        # id -> hash, rebuilt lazily by _tree()
        self._tree_dirty = True
        self._nodes: dict[str, str] = {}

    def add_object(self, knowledge_object: Any) -> bool:
        object_id = object_id_for(knowledge_object)
        if object_id in self._objects:
            return False
        self._objects[object_id] = _serialize_object(knowledge_object, object_id)
        self._tree_dirty = True
        return True

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        return self._objects.get(object_id)

    def list_objects(self) -> list[dict[str, Any]]:
        return list(self._objects.values())

    def merge_objects(self, objects: list[Any]) -> int:
        return sum(1 for obj in objects if self.add_object(obj))

    def _rebuild_tree(self) -> None:
        from cks_runtime.crdt.merkle_tree import (
            _ID_HEX_LENGTH,
            EMPTY_SUBTREE_HASH,
            _node_hash,
        )

        nodes: dict[str, str] = {}
        for object_id in self._objects:
            nodes[object_id] = object_id  # level 64
        for level in range(_ID_HEX_LENGTH - 1, -1, -1):
            prefixes = {oid[:level] for oid in self._objects}
            for prefix in prefixes:
                children = [
                    nodes.get(prefix + nibble, EMPTY_SUBTREE_HASH)
                    for nibble in "0123456789abcdef"
                ]
                nodes[prefix] = _node_hash(prefix, children)
        if not self._objects:
            nodes[""] = EMPTY_SUBTREE_HASH
        self._nodes = nodes
        self._tree_dirty = False

    def get_root_hash(self) -> str:
        if self._tree_dirty:
            self._rebuild_tree()
        from cks_runtime.crdt.merkle_tree import EMPTY_SUBTREE_HASH

        return self._nodes.get("", EMPTY_SUBTREE_HASH)

    def get_children_hashes(self, prefix: str) -> list[str]:
        if self._tree_dirty:
            self._rebuild_tree()
        from cks_runtime.crdt.merkle_tree import EMPTY_SUBTREE_HASH

        return [self._nodes.get(prefix + n, EMPTY_SUBTREE_HASH) for n in "0123456789abcdef"]

    def update_version_vector(self, node_id: str, vv: VersionVector) -> None:
        self._vectors[node_id] = VersionVector(clocks=dict(vv.clocks))

    def get_version_vector(self, node_id: str) -> VersionVector:
        return self._vectors.get(node_id, VersionVector())


#: Union type alias for call sites that accept any sync backend.
CRDTStore = SQLiteCRDTStore | InMemoryCRDTStore
