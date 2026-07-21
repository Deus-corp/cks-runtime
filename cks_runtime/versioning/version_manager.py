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

from typing import TYPE_CHECKING, Any

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
        previous_state: Any | None = None,
    ) -> RuntimeVersion:
        """
        Create a new RuntimeVersion.

        The created version is automatically attached
        to the owning RuntimeSession.

        Whether the resulting version stores its Knowledge Structure
        directly ("snapshot") or only a ``patch`` to be replayed from
        the nearest earlier snapshot is decided *here, once, at
        creation time* -- not later on read. A version is a snapshot
        when any of the following holds:

        - no ``core_bridge`` was given (nothing capable of computing
          or replaying a patch is available, so a full copy is the
          only option);
        - this is the session's first version (index 0 -- there is no
          earlier snapshot to replay from);
        - ``len(session.version_history)`` is a multiple of
          ``session.snapshot_interval``.

        Otherwise a delta version is recorded: ``knowledge_structure``
        is left ``None`` and ``patch`` holds
        ``core_bridge.diff(previous_state, session.knowledge_structure)``.
        Callers must reconstruct such versions via
        :meth:`RuntimeSession.get_version_state`, not by reading
        ``knowledge_structure`` directly.

        Parameters
        ----------
        core_bridge
            Optional. When supplied and the attached Core
            implementation supports content hashing, the resulting
            version's ``state_hash`` is populated regardless of
            whether it ends up a snapshot or a delta. Left as
            ``None`` when no bridge is given, no Core is attached, or
            the Core does not implement ``hash()`` — this is treated
            as "no integrity hash available", not an error.
        previous_state
            Required (and must not be ``None``) whenever this call
            produces a delta version, i.e. whenever a ``core_bridge``
            is given and this is *not* the first version and not a
            snapshot-interval boundary: the session's Knowledge
            Structure as it was immediately before the transaction
            being recorded, used to compute ``patch``. Ignored for
            snapshot versions, so callers that always pass it (e.g.
            "state of the session at the start of every commit") do
            not need to special-case anything.

        Raises
        ------
        ValueError
            A delta version is being recorded but ``previous_state``
            was not supplied.
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

        index = len(session.version_history)
        is_snapshot = (
            core_bridge is None
            or index == 0
            or index % session.snapshot_interval == 0
        )

        if is_snapshot:
            version = RuntimeVersion(
                session_id=session.session_id,
                transaction_id=transaction_id,
                knowledge_structure=session.knowledge_structure,
                metadata=session.metadata,
                state_hash=state_hash,
            )
        else:
            if previous_state is None:
                raise ValueError(
                    "previous_state is required to record a delta "
                    "(non-snapshot) version: pass the session's "
                    "Knowledge Structure as it was immediately before "
                    "this transaction's mutations."
                )
            patch = core_bridge.diff(previous_state, session.knowledge_structure)
            version = RuntimeVersion(
                session_id=session.session_id,
                transaction_id=transaction_id,
                knowledge_structure=None,
                metadata=session.metadata,
                state_hash=state_hash,
                patch=patch,
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