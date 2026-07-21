"""
Runtime Version Manager.

Owns RuntimeVersion lifecycle.

Responsibilities:

- create RuntimeVersions;
- retrieve RuntimeVersions;
- enumerate RuntimeVersions.

Does not own:

- persistence;
- semantic validation;
- transactions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cks_runtime.session.session import RuntimeSession

from .version import RuntimeVersion

if TYPE_CHECKING:
    from cks_runtime.core_api.bridge import CoreBridge


class VersionManager:
    """
    Owns RuntimeVersion lifecycle.
    """

    #
    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    #

    def create(
        self,
        session: RuntimeSession,
        core_bridge: "CoreBridge | None" = None,
    ) -> RuntimeVersion:
        """
        Create a new RuntimeVersion.

        The created version is automatically attached
        to the owning RuntimeSession.

        Parameters
        ----------
        core_bridge
            Optional. When supplied and the attached Core
            implementation supports content hashing, the resulting
            version's ``state_hash`` is populated. Left as ``None``
            when no bridge is given, no Core is attached, or the
            Core does not implement ``hash()`` — this is treated as
            "no integrity hash available", not an error.
        """

        transaction_id = ""

        if session.has_active_transaction:
            transaction_id = (
                session.active_transaction.transaction_id
            )

        state_hash: str | None = None
        if core_bridge is not None:
            try:
                state_hash = core_bridge.hash(session.knowledge_structure)
            except (NotImplementedError, RuntimeError):
                state_hash = None

        version = RuntimeVersion(
            session_id=session.session_id,
            transaction_id=transaction_id,
            knowledge_structure=session.knowledge_structure,
            metadata=session.metadata,
            state_hash=state_hash,
        )

        session.add_version(
            version,
        )

        return version

    #
    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    #

    def latest(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion | None:
        """
        Return the latest RuntimeVersion.

        Returns None when no versions exist.
        """

        if not session.has_versions:
            return None

        return session.version_history[-1]

    def retrieve(
        self,
        session: RuntimeSession,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Retrieve a RuntimeVersion by identifier.
        """

        for version in session.version_history:
            if version.version_id == version_id:
                return version

        return None

    def has_versions(
        self,
        session: RuntimeSession,
    ) -> bool:
        """
        Whether the RuntimeSession contains versions.
        """

        return session.has_versions

    def version_count(
        self,
        session: RuntimeSession,
    ) -> int:
        """
        Number of RuntimeVersions belonging
        to the RuntimeSession.
        """

        return len(
            session.version_history,
        )

    def list_versions(
        self,
        session: RuntimeSession,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return immutable RuntimeVersion history.
        """

        return tuple(
            session.version_history,
        )

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    def clear(
        self,
        session: RuntimeSession,
    ) -> None:
        """
        Remove every RuntimeVersion.

        Primarily intended for testing.
        """

        session.version_history.clear()