"""
Runtime Storage Interface.

Defines the persistence boundary for Runtime operational state.

Storage implementations persist Runtime objects but never
own Runtime behaviour or semantic interpretation.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion


class RuntimeStorage(ABC):
    """
    Abstract Runtime storage.

    Storage is responsible only for persistence.

    Storage never:

    - owns RuntimeSessions;
    - owns RuntimeTransactions;
    - owns RuntimeVersions;
    - performs semantic validation.
    """

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_session(
        self,
        session: RuntimeSession,
    ) -> None:
        """
        Persist a RuntimeSession.
        """

    @abstractmethod
    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Restore a RuntimeSession.

        Returns None when the session does not exist.
        """

    @abstractmethod
    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

    @abstractmethod
    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return every persisted RuntimeSession.
        """

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a RuntimeVersion.
        """

    @abstractmethod
    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Restore a RuntimeVersion.

        Returns None when the version does not exist.
        """

    @abstractmethod
    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Whether a RuntimeVersion exists.
        """

    @abstractmethod
    def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return every persisted RuntimeVersion.
        """

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def clear(self) -> None:
        """
        Remove every persisted object.

        Primarily intended for testing and
        reference implementations.
        """