"""
In-memory Runtime Storage.

Reference implementation used by tests.
"""

from __future__ import annotations

from copy import deepcopy

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.exceptions import (
    SessionNotFound,
    VersionNotFound,
)
from cks_runtime.storage.storage import RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


class InMemoryStorage(RuntimeStorage):
    """
    Reference Runtime Storage implementation.

    Provides deterministic in-memory persistence for:

    - Runtime Sessions;
    - Runtime Versions.

    Intended primarily for testing and as the
    reference Runtime Storage implementation.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}
        self._versions: dict[str, RuntimeVersion] = {}

    def save_session(
        self,
        session: RuntimeSession,
    ) -> None:
        """
        Persist a Runtime Session.
        """

        self._sessions[
            session.session_id
        ] = deepcopy(session)

    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession:
        """
        Restore a Runtime Session.
        """

        try:
            session = self._sessions[
                session_id
            ]
        except KeyError as exc:
            raise SessionNotFound(
                session_id
            ) from exc

        return deepcopy(session)

    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a Runtime Version.
        """

        self._versions[
            version.version_id
        ] = deepcopy(version)

    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion:
        """
        Restore a Runtime Version.
        """

        try:
            version = self._versions[
                version_id
            ]
        except KeyError as exc:
            raise VersionNotFound(
                version_id
            ) from exc

        return deepcopy(version)

    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Determine whether a Runtime Session exists.
        """

        return session_id in self._sessions


    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Determine whether a Runtime Version exists.
        """

        return version_id in self._versions


    def clear(self) -> None:
        """
        Remove every persisted Runtime object.
        """

        self._sessions.clear()
        self._versions.clear()