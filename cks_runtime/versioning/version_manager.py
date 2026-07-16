"""
Runtime Version Manager.

Creates immutable Runtime Versions from the
current Runtime Session state.
"""

from __future__ import annotations

from cks_runtime.session.session import RuntimeSession

from .version import RuntimeVersion


class VersionManager:
    """
    Coordinates Runtime Version lifecycle.
    """

    def create(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion:
        """
        Create a new Runtime Version.

        The Version becomes part of the owning
        Runtime Session history.
        """

        transaction_id = ""

        if (
            session.active_transaction
            is not None
        ):
            transaction_id = (
                session.active_transaction
                .transaction_id
            )

        version = RuntimeVersion(
            session_id=session.session_id,
            transaction_id=transaction_id,
            knowledge_structure=session.knowledge_structure,
            metadata=session.metadata,
        )

        session.version_history.append(
            version
        )

        return version

    def latest(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion | None:
        """
        Return the latest Runtime Version.
        """

        if not session.version_history:
            return None

        return session.version_history[-1]

    def retrieve(
        self,
        session: RuntimeSession,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Retrieve a Runtime Version
        by identifier.
        """

        for version in (
            session.version_history
        ):
            if (
                version.version_id
                == version_id
            ):
                return version

        return None

    def list_versions(
        self,
        session: RuntimeSession,
    ) -> tuple[
        RuntimeVersion,
        ...
    ]:
        """
        Return immutable Runtime
        Version history.
        """

        return tuple(
            session.version_history
        )