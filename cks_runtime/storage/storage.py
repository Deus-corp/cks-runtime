"""
Abstract Runtime Storage.

SPEC-006 Storage.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion


class RuntimeStorage(ABC):
    """
    Abstract Runtime Storage.

    Storage persists Runtime operational state.

    Storage never:

    - owns Sessions;
    - owns Transactions;
    - interprets semantics.
    """

    @abstractmethod
    def save_session(
        self,
        session: RuntimeSession,
    ) -> None:
        """
        Persist a Runtime Session.
        """

    @abstractmethod
    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession:
        """
        Restore a Runtime Session.
        """

    @abstractmethod
    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a Runtime Version.
        """

    @abstractmethod
    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion:
        """
        Restore a Runtime Version.
        """