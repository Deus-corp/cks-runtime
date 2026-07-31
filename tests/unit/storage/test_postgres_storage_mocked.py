"""
Mocked (no live DB) regression tests for PostgresStorage.

test_postgres_storage.py exercises PostgresStorage end-to-end but is
entirely skipped unless CKS_TEST_POSTGRES_DSN is set, which means it
never runs in an environment without a real Postgres instance
available (e.g. plain `pytest` in CI without a DB service). That gap
let a real bug ship: record_operations() called `conn.executemany(...)`
directly, but psycopg3's AsyncConnection has no such method -- only
AsyncCursor does -- so every call raised AttributeError against a real
driver.

These tests use `unittest.mock.AsyncMock(spec=psycopg.AsyncConnection)`
so that referencing an attribute the real driver doesn't have fails
the same way it would against a live connection, without needing one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("psycopg")

import psycopg

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.storage.postgres_storage import PostgresStorage

pytestmark = pytest.mark.asyncio


def _mock_pool(conn: AsyncMock) -> MagicMock:
    """A pool whose `.connection()` async-context-manager yields `conn`."""
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = False
    pool = MagicMock()
    pool.connection.return_value = cm
    return pool


async def test_record_operations_uses_cursor_executemany_not_connection():
    """
    record_operations() must call executemany() on a cursor, not on
    the connection directly -- AsyncConnection has no executemany.

    spec=psycopg.AsyncConnection means conn.executemany(...) raises
    AttributeError here exactly as it would against a real driver, so
    this test fails on the original bug without needing a live DB.
    """
    cursor_cm = AsyncMock(spec=psycopg.AsyncCursor)
    cursor_cm.__aenter__.return_value = cursor_cm
    cursor_cm.__aexit__.return_value = False

    conn = AsyncMock(spec=psycopg.AsyncConnection)
    conn.cursor = MagicMock(return_value=cursor_cm)  # cursor() is sync in psycopg3

    storage = PostgresStorage(_mock_pool(conn))

    operations = [
        RuntimeFieldOperation(
            object_id="obj-1",
            op_type="set_field",
            field_key="name",
            field_value="Alpha",
        )
    ]

    await storage.record_operations(
        session_id="sess-1",
        version_id="ver-1",
        operations=operations,
    )

    cursor_cm.executemany.assert_awaited_once()
    conn.commit.assert_awaited_once()


async def test_record_operations_empty_list_is_noop():
    """No operations -> no connection borrowed at all."""
    conn = AsyncMock(spec=psycopg.AsyncConnection)
    pool = _mock_pool(conn)
    storage = PostgresStorage(pool)

    await storage.record_operations(session_id="sess-1", version_id="ver-1", operations=[])

    pool.connection.assert_not_called()
