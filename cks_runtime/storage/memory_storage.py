"""
In-memory Runtime Storage.

Reference RuntimeStorage implementation.

Provides deterministic in-memory persistence for Runtime
objects and is primarily intended for testing.
"""

from __future__ import annotations

from copy import deepcopy

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


class InMemoryStorage(RuntimeStorage):
    """
    Reference RuntimeStorage implementation.

    Persists Runtime state entirely in memory.

    Objects are always deep-copied on both save and load
    to preserve snapshot semantics and avoid shared mutable
    state. This follows the same rationale used by immutable
    RuntimeVersion snapshots. :contentReference[oaicite:0]{index=0}
    """

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}
        self._versions: dict[str, RuntimeVersion] = {}

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        """
        Persist a RuntimeSession.

        Honors the same ``expected_version_id`` CAS contract as
        SQLiteStorage, checked against the previously-saved copy's
        latest version -- see ``RuntimeStorage.save_session``.
        """

        if expected_version_id is not None:
            current = self._sessions.get(session.session_id)
            current_latest = (
                current.version_history[-1].version_id
                if current is not None and current.version_history
                else None
            )
            if current_latest != expected_version_id:
                from cks_runtime.storage.storage import ConcurrentModificationError
                raise ConcurrentModificationError(session.session_id)

        self._sessions[
            session.session_id
        ] = deepcopy(session)

    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Restore a RuntimeSession.

        Returns
        -------
        RuntimeSession | None
            Stored session, if present.
        """

        session = self._sessions.get(
            session_id,
        )

        if session is None:
            return None

        return deepcopy(session)

    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

        return session_id in self._sessions

    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return every persisted RuntimeSession.
        """

        return tuple(
            deepcopy(
                tuple(
                    self._sessions.values(),
                )
            )
        )

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a RuntimeVersion.
        """

        self._versions[
            version.version_id
        ] = deepcopy(version)

    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Restore a RuntimeVersion.

        Returns
        -------
        RuntimeVersion | None
            Stored version, if present.
        """

        version = self._versions.get(
            version_id,
        )

        if version is None:
            return None

        return deepcopy(version)

    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Whether a RuntimeVersion exists.
        """

        return version_id in self._versions

    def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return every persisted RuntimeVersion.
        """

        return tuple(
            deepcopy(
                tuple(
                    self._versions.values(),
                )
            )
        )

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    def clear(self) -> None:
        """
        Remove every persisted Runtime object.
        """

        self._sessions.clear()
        self._versions.clear()