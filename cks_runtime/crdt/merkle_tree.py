"""
Merkle prefix tree over CRDT knowledge-object ids (ADR-013, Stage 1).

Each ``cks_knowledge_objects.id`` is a 64-character lowercase hex
SHA-256 digest (see ``crdt_store.CRDTStore.add_object``). This module
builds a radix-16 prefix tree over those hex ids: level ``L`` (for
``L`` in ``0..64``) has one node per distinct ``L``-character prefix
that occurs among stored ids, plus level 0's single node (the empty
prefix, i.e. the root). Level 64 nodes correspond 1:1 to leaves (full
ids); every other level's node hash is the SHA-256 of the
concatenation of its (up to) 16 children's hashes, in nibble order
0..f, with a well-known empty-subtree hash standing in for any
missing child.

Incremental update
-------------------
Inserting one object only ever touches the single root-to-leaf path
for its id: exactly 65 nodes (level 64 down to level 0). Recomputing
those from the leaf up is `update_merkle_path`; it never touches any
other node in the tree, which is what keeps a single insert O(1) in
tree depth rather than O(number of objects).

Two backends are provided:

- ``SQLiteMerkleTree`` -- synchronous, computes and upserts the path
  in Python on every insert (SQLite has no stored procedures).
- ``PostgresMerkleTree`` -- async; the same incremental recomputation
  is also expressed as a PL/pgSQL trigger function
  (``update_merkle_tree_on_insert``) attached to
  ``cks_knowledge_objects`` so a direct SQL ``INSERT`` (from any
  client, not just this adapter) keeps the tree consistent. The
  Python methods here still work for callers that want direct access
  from application code (e.g. gossip reconciliation), and use the
  same nibble-path algorithm as the trigger for consistency.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

#: SHA-256 hex digests are 64 characters long -- levels run 0 (root,
#: empty prefix) through 64 (a full leaf id).
_ID_HEX_LENGTH = 64
_NIBBLES = "0123456789abcdef"

#: Hash standing in for a child subtree that has no objects under it
#: yet, so a node with fewer than 16 present children still has a
#: fully-defined, order-independent hash. Distinct from any real leaf
#: hash (SHA-256 of the ASCII marker, not of empty bytes, to avoid
#: colliding with sha256(b"") were that ever used as a real id).
EMPTY_SUBTREE_HASH = hashlib.sha256(b"cks-crdt-merkle-empty-subtree").hexdigest()


def _validate_id(object_id: str) -> None:
    if (
        len(object_id) != _ID_HEX_LENGTH
        or any(c not in _NIBBLES for c in object_id.lower())
    ):
        raise ValueError(
            f"MerkleTree object ids must be {_ID_HEX_LENGTH}-character lowercase "
            f"hex SHA-256 digests, got {object_id!r}"
        )


def _node_hash(prefix: str, children: list[str]) -> str:
    """Hash a node from its (already-computed) children's hashes."""
    payload = prefix.encode("ascii") + "".join(children).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class SQLiteMerkleTree:
    """
    Synchronous Merkle prefix tree backed by a SQLite table.

    Shares its connection with the ``SQLiteCRDTStore`` that owns it
    (same pattern as ``SQLiteStorage``: one persistent connection,
    WAL mode, caller-provided retry wrapper for "database is locked").
    """

    def __init__(self, conn: sqlite3.Connection, retry: Callable[..., Any]) -> None:
        self._conn = conn
        # `retry` is the `_retry_on_locked`-shaped callable from
        # sqlite_storage.py, injected by CRDTStore rather than
        # duplicated here.
        self._retry = retry
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cks_merkle_tree (
                prefix_path TEXT PRIMARY KEY,
                level       INTEGER NOT NULL,
                hash        TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cks_merkle_tree_level "
            "ON cks_merkle_tree(level)"
        )
        self._conn.commit()

    # -- reads -----------------------------------------------------------

    def _get_hash(self, prefix: str) -> str:
        cur = self._conn.execute(
            "SELECT hash FROM cks_merkle_tree WHERE prefix_path = ?", (prefix,)
        )
        row = cur.fetchone()
        return row[0] if row is not None else EMPTY_SUBTREE_HASH

    def get_root_hash(self) -> str:
        """Return the hash of the root node (prefix_path = '')."""
        return self._get_hash("")

    def get_children_hashes(self, prefix_path: str) -> list[str]:
        """
        Return the 16 child-node hashes (nibbles '0'..'f', in order)
        for ``prefix_path``, for gossip-side subtree comparison.
        Missing children come back as ``EMPTY_SUBTREE_HASH``.
        """
        return [self._get_hash(prefix_path + nibble) for nibble in _NIBBLES]

    # -- writes ------------------------------------------------------------

    def update_merkle_path(self, object_id: str) -> None:
        """
        Recompute every node on the root-to-leaf path for
        ``object_id`` (65 upserts: level 64 down to level 0).
        """
        _validate_id(object_id)
        object_id = object_id.lower()

        def _write() -> None:
            # Level 64: the leaf. Its hash is the object id itself --
            # already a content hash, so there is nothing further to
            # combine at the leaf level.
            self._upsert(object_id, _ID_HEX_LENGTH, object_id)
            # Levels 63 .. 0: each node's hash is derived from its 16
            # children (one level down), which were just written (or
            # already existed) above.
            for level in range(_ID_HEX_LENGTH - 1, -1, -1):
                prefix = object_id[:level]
                children = self.get_children_hashes(prefix)
                self._upsert(prefix, level, _node_hash(prefix, children))
            self._conn.commit()

        self._retry(_write)

    def _upsert(self, prefix: str, level: int, node_hash: str) -> None:
        self._conn.execute(
            """
            INSERT INTO cks_merkle_tree (prefix_path, level, hash, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(prefix_path) DO UPDATE SET
                hash = excluded.hash,
                updated_at = excluded.updated_at
            """,
            (prefix, level, node_hash),
        )


class PostgresMerkleTree:
    """
    Async Merkle prefix tree backed by a PostgreSQL table, kept
    consistent by a PL/pgSQL trigger (``DDL_MERKLE_TRIGGER`` below) on
    ``cks_knowledge_objects`` inserts. The Python methods here read
    through the pool and can also drive an update directly (e.g. from
    ``PostgresCRDTStore.add_object``, for backends/tests that create
    the table without the trigger installed).
    """

    def __init__(self, pool: AsyncConnectionPool, retry: Callable[..., Any]) -> None:
        self._pool = pool
        self._retry = retry

    async def ensure_schema(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(DDL_MERKLE_TREE)
            await conn.execute(DDL_MERKLE_TREE_LEVEL_INDEX)

    async def _get_hash(self, prefix: str) -> str:
        async def _read() -> str:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT hash FROM cks_merkle_tree WHERE prefix_path = %s",
                    (prefix,),
                )
                row = await cur.fetchone()
                return row[0] if row is not None else EMPTY_SUBTREE_HASH

        return await self._retry(_read)

    async def get_root_hash(self) -> str:
        return await self._get_hash("")

    async def get_children_hashes(self, prefix_path: str) -> list[str]:
        async def _read() -> list[str]:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT prefix_path, hash FROM cks_merkle_tree "
                    "WHERE level = %s AND prefix_path LIKE %s",
                    (len(prefix_path) + 1, prefix_path + "%"),
                )
                rows = {r[0]: r[1] async for r in cur}
            return [
                rows.get(prefix_path + nibble, EMPTY_SUBTREE_HASH)
                for nibble in _NIBBLES
            ]

        return await self._retry(_read)

    async def update_merkle_path(self, object_id: str) -> None:
        """
        Python-side fallback path recomputation, used when the DB
        trigger isn't installed (e.g. a bare table created only via
        ``ensure_schema``). Idempotent with the trigger: both compute
        the same hashes for the same tree state.
        """
        _validate_id(object_id)
        object_id = object_id.lower()

        async def _write() -> None:
            async with self._pool.connection() as conn:
                await self._upsert(conn, object_id, _ID_HEX_LENGTH, object_id)
                for level in range(_ID_HEX_LENGTH - 1, -1, -1):
                    prefix = object_id[:level]
                    children = []
                    for nibble in _NIBBLES:
                        cur = await conn.execute(
                            "SELECT hash FROM cks_merkle_tree WHERE prefix_path = %s",
                            (prefix + nibble,),
                        )
                        row = await cur.fetchone()
                        children.append(row[0] if row is not None else EMPTY_SUBTREE_HASH)
                    await self._upsert(conn, prefix, level, _node_hash(prefix, children))
                await conn.commit()

        await self._retry(_write)

    async def _upsert(self, conn: object, prefix: str, level: int, node_hash: str) -> None:
        await conn.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO cks_merkle_tree (prefix_path, level, hash, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (prefix_path) DO UPDATE SET
                hash = EXCLUDED.hash,
                updated_at = EXCLUDED.updated_at
            """,
            (prefix, level, node_hash),
        )


# ---------------------------------------------------------------------------
# PostgreSQL DDL: table, index, and trigger-based incremental maintenance.
# ---------------------------------------------------------------------------

DDL_MERKLE_TREE = """
    CREATE TABLE IF NOT EXISTS cks_merkle_tree (
        prefix_path VARCHAR(64) PRIMARY KEY,
        level       INT NOT NULL,
        hash        VARCHAR(64) NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

DDL_MERKLE_TREE_LEVEL_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_cks_merkle_tree_level
    ON cks_merkle_tree(level)
"""

#: PL/pgSQL trigger function: on INSERT into cks_knowledge_objects,
#: recompute the 65-node root-to-leaf path for NEW.id, bottom-up.
#: Mirrors SQLiteMerkleTree.update_merkle_path exactly (same empty-
#: subtree sentinel, same node-hash construction) so the two backends
#: converge on identical root hashes for identical object sets --
#: required for cross-backend gossip reconciliation.
DDL_MERKLE_TRIGGER_FUNCTION = f"""
    CREATE OR REPLACE FUNCTION update_merkle_tree_on_insert()
    RETURNS TRIGGER AS $$
    DECLARE
        obj_id TEXT := lower(NEW.id);
        lvl INT;
        prefix TEXT;
        child_hashes TEXT[];
        nibble TEXT;
        child_hash TEXT;
        new_hash TEXT;
        empty_hash TEXT := '{EMPTY_SUBTREE_HASH}';
    BEGIN
        -- Level 64 (the leaf): hash is the object id itself.
        INSERT INTO cks_merkle_tree (prefix_path, level, hash, updated_at)
        VALUES (obj_id, 64, obj_id, now())
        ON CONFLICT (prefix_path) DO UPDATE SET
            hash = EXCLUDED.hash, updated_at = EXCLUDED.updated_at;

        -- Levels 63 down to 0: derive each node from its 16 children.
        FOR lvl IN REVERSE 63..0 LOOP
            prefix := substring(obj_id FROM 1 FOR lvl);
            child_hashes := ARRAY[]::TEXT[];
            FOREACH nibble IN ARRAY ARRAY['0','1','2','3','4','5','6','7',
                                           '8','9','a','b','c','d','e','f']
            LOOP
                SELECT hash INTO child_hash FROM cks_merkle_tree
                WHERE prefix_path = prefix || nibble;
                IF child_hash IS NULL THEN
                    child_hash := empty_hash;
                END IF;
                child_hashes := array_append(child_hashes, child_hash);
            END LOOP;

            new_hash := encode(
                digest(prefix || array_to_string(child_hashes, ''), 'sha256'),
                'hex'
            );

            INSERT INTO cks_merkle_tree (prefix_path, level, hash, updated_at)
            VALUES (prefix, lvl, new_hash, now())
            ON CONFLICT (prefix_path) DO UPDATE SET
                hash = EXCLUDED.hash, updated_at = EXCLUDED.updated_at;
        END LOOP;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""

#: Requires the pgcrypto extension for `digest()`. Callers should run
#: ``CREATE EXTENSION IF NOT EXISTS pgcrypto`` once per database
#: before installing this trigger (see PostgresCRDTStore.ensure_schema).
DDL_MERKLE_TRIGGER = """
    DROP TRIGGER IF EXISTS trg_update_merkle_tree ON cks_knowledge_objects;
    CREATE TRIGGER trg_update_merkle_tree
    AFTER INSERT ON cks_knowledge_objects
    FOR EACH ROW EXECUTE FUNCTION update_merkle_tree_on_insert();
"""

DDL_GET_ROOT_HASH_FUNCTION = """
    CREATE OR REPLACE FUNCTION get_root_hash() RETURNS VARCHAR(64) AS $$
        SELECT hash FROM cks_merkle_tree WHERE prefix_path = '';
    $$ LANGUAGE sql STABLE;
"""

DDL_GET_CHILDREN_HASHES_FUNCTION = """
    CREATE OR REPLACE FUNCTION get_children_hashes(p_prefix VARCHAR(64))
    RETURNS TABLE(nibble CHAR(1), hash VARCHAR(64)) AS $$
        SELECT n.nibble, COALESCE(t.hash, '""" + EMPTY_SUBTREE_HASH + """')
        FROM unnest(ARRAY['0','1','2','3','4','5','6','7',
                           '8','9','a','b','c','d','e','f']) AS n(nibble)
        LEFT JOIN cks_merkle_tree t
            ON t.prefix_path = p_prefix || n.nibble;
    $$ LANGUAGE sql STABLE;
"""

#: Convenience bundle for a single ``ensure_schema`` call site.
DDL_STATEMENTS: tuple[str, ...] = (
    DDL_MERKLE_TREE,
    DDL_MERKLE_TREE_LEVEL_INDEX,
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    DDL_MERKLE_TRIGGER_FUNCTION,
    DDL_MERKLE_TRIGGER,
    DDL_GET_ROOT_HASH_FUNCTION,
    DDL_GET_CHILDREN_HASHES_FUNCTION,
)
