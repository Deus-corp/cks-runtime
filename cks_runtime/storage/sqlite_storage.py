"""
SQLite-backed Runtime Storage.

Persists sessions and versions to a SQLite database file.
Uses pickle for arbitrary Python objects (compatible with any Core).
"""

from __future__ import annotations

import pickle
import sqlite3
from typing import Optional

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


class SQLiteStorage(RuntimeStorage):
    """Persists Runtime state in a SQLite database."""

    def __init__(self, db_path: str = "cks_runtime.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data BLOB NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS versions (
                version_id TEXT PRIMARY KEY,
                data BLOB NOT NULL
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(self, session: RuntimeSession) -> None:
        blob = pickle.dumps(session)
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, data) VALUES (?, ?)",
            (session.session_id, blob),
        )
        self._conn.commit()

    def load_session(self, session_id: str) -> Optional[RuntimeSession]:
        row = self._conn.execute(
            "SELECT data FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return pickle.loads(row[0])

    def has_session(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def list_sessions(self) -> tuple[RuntimeSession, ...]:
        rows = self._conn.execute("SELECT data FROM sessions").fetchall()
        return tuple(pickle.loads(row[0]) for row in rows)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def save_version(self, version: RuntimeVersion) -> None:
        blob = pickle.dumps(version)
        self._conn.execute(
            "INSERT OR REPLACE INTO versions (version_id, data) VALUES (?, ?)",
            (version.version_id, blob),
        )
        self._conn.commit()

    def load_version(self, version_id: str) -> Optional[RuntimeVersion]:
        row = self._conn.execute(
            "SELECT data FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            return None
        return pickle.loads(row[0])

    def has_version(self, version_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return row is not None

    def list_versions(self) -> tuple[RuntimeVersion, ...]:
        rows = self._conn.execute("SELECT data FROM versions").fetchall()
        return tuple(pickle.loads(row[0]) for row in rows)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self._conn.execute("DELETE FROM sessions")
        self._conn.execute("DELETE FROM versions")
        self._conn.commit()