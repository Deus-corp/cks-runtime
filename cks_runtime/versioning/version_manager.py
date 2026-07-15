"""
Runtime Version Manager.

Coordinates Runtime Version creation and retrieval.

Runtime Versions remain owned by Runtime Sessions.
"""

from __future__ import annotations

from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion


class VersionManager:
    """
    Coordinates Runtime Version lifecycle.

    Ownership:

    - RuntimeSession owns Runtime Versions.
    - VersionManager coordinates Version operations.
    """

    def create(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion:
        """
        Create a new Runtime Version.

        The Version is appended to the owning
        Session Version History.
        """

        version = RuntimeVersion()

        session.version_history.append(version)

        return version

    def retrieve(
        self,
        session: RuntimeSession,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Retrieve a Version by identity.
        """

        for version in session.version_history:
            if version.version_id == version_id:
                return version

        return None

    def latest(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion | None:
        """
        Return the latest Runtime Version.

        Returns None when no Versions exist.
        """

        if not session.version_history:
            return None

        return session.version_history[-1]

    def list_versions(
        self,
        session: RuntimeSession,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return an immutable snapshot
        of Version History.
        """

        return tuple(
            session.version_history
        )