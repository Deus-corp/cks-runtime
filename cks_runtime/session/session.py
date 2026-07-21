"""
Runtime Session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class RuntimeSession:
    knowledge_structure: Any
    session_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Any] = field(default_factory=list)
    version_history: list[Any] = field(default_factory=list)
    active_transaction: Any | None = None
    closed: bool = False

    @property
    def is_active(self) -> bool:
        return not self.closed

    @property
    def has_active_transaction(self) -> bool:
        return self.active_transaction is not None

    @property
    def version_count(self) -> int:
        return len(self.version_history)

    @property
    def has_versions(self) -> bool:
        return bool(self.version_history)

    def close(self) -> None:
        self.closed = True

    def add_diagnostic(self, diagnostic: Any) -> None:
        self.diagnostics.append(diagnostic)

    def add_version(self, version: Any) -> None:
        self.version_history.append(version)

    def attach_transaction(self, transaction: Any) -> None:
        self.active_transaction = transaction

    def detach_transaction(self) -> None:
        self.active_transaction = None
    
    def get_version_state(
        self,
        version_id: str,
        core_bridge: Any,
        *,
        snapshot_every: int = 10,
    ) -> Any:
        """
        Reconstruct the Knowledge Structure for a specific version.

        Rather than trusting whichever full structure happens to be
        stored on the target ``RuntimeVersion`` directly, every
        version whose index is not a multiple of ``snapshot_every``
        (nor the very first one) is recomputed by replaying
        incremental diffs forward from the nearest earlier
        "snapshot" version: ``core_bridge.diff()`` is used to compute
        the patch between each pair of consecutive stored states, and
        ``core_bridge.evolve()`` is used to replay it. This is the
        reconstruction path a future patch-only version history would
        rely on instead of storing a full copy per version; it is
        exercised here against today's fully-materialized storage so
        it can be verified correct (via ``state_hash``, when the
        attached Core supports hashing) before anything stops storing
        full copies.

        Parameters
        ----------
        version_id
            Identifier of the version to reconstruct.
        core_bridge
            Anything exposing ``diff(source, target)`` and
            ``evolve(structure, patch)`` — typically a
            :class:`~cks_runtime.core_api.bridge.CoreBridge`. Its
            optional ``hash()`` is used for an integrity check when
            the target version has a recorded ``state_hash``.
        snapshot_every
            How many versions apart a stored state is trusted
            directly instead of being replayed. Must be >= 1.

        Raises
        ------
        ValueError
            ``version_id`` does not exist in this session; a stored
            checkpoint's own recorded hash doesn't match its own
            stored structure (storage-level tampering/corruption); or
            the final reconstructed state's hash doesn't match the
            target version's recorded hash (a reconstruction-level
            divergence).

        Notes
        -----
        Each traversed version's stored full structure is checked
        against *its own* recorded ``state_hash`` as it is visited,
        not only the final target. Patches are computed directly
        between consecutive *stored* states (``core_bridge.diff()``
        between ``version_history[i - 1]`` and ``version_history[i]``
        rather than between the running reconstruction and the next
        stored state) — this means an isolated corrupted checkpoint
        would otherwise "self-heal" one hop later purely because the
        next patch is computed relative to the (corrupted) stored
        endpoint rather than the true prior state. Verifying every
        checkpoint as it is visited closes that gap.
        """
        if snapshot_every < 1:
            raise ValueError("snapshot_every must be >= 1")

        index = self._version_index(version_id)
        target_version = self.version_history[index]

        snapshot_index = (index // snapshot_every) * snapshot_every

        def verify_checkpoint(version: Any, structure: Any) -> None:
            if version.state_hash is None:
                return
            try:
                actual_hash = core_bridge.hash(structure)
            except NotImplementedError:
                return
            if actual_hash != version.state_hash:
                raise ValueError(
                    f"Stored state for version {version.version_id!r} does "
                    f"not match its recorded hash (expected "
                    f"{version.state_hash!r}, got {actual_hash!r}). "
                    f"History may be corrupted or tampered with."
                )

        snapshot_version = self.version_history[snapshot_index]
        state = snapshot_version.knowledge_structure
        verify_checkpoint(snapshot_version, state)

        for i in range(snapshot_index + 1, index + 1):
            previous_version = self.version_history[i - 1]
            current_version = self.version_history[i]
            verify_checkpoint(current_version, current_version.knowledge_structure)

            patch = core_bridge.diff(
                previous_version.knowledge_structure,
                current_version.knowledge_structure,
            )
            state = core_bridge.evolve(state, patch)

        if target_version.state_hash is not None:
            try:
                actual_hash = core_bridge.hash(state)
            except NotImplementedError:
                actual_hash = None
            if actual_hash is not None and actual_hash != target_version.state_hash:
                raise ValueError(
                    f"Reconstructed state for version {version_id!r} does "
                    f"not match its recorded hash (expected "
                    f"{target_version.state_hash!r}, got {actual_hash!r})."
                )

        return state

    def _version_index(self, version_id: str) -> int:
        for i, version in enumerate(self.version_history):
            if version.version_id == version_id:
                return i
        raise ValueError(f"Version {version_id!r} not found in session history.")