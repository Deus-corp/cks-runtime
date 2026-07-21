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
    snapshot_interval: int = 10
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
        core_bridge: Any = None,
    ) -> Any:
        """
        Reconstruct the Knowledge Structure for a specific version.

        Snapshot versions (``version.is_snapshot``) have their full
        Knowledge Structure returned directly. Delta versions have
        no stored structure at all: this method walks backward to
        the nearest earlier snapshot, then replays each intervening
        version's stored ``patch`` forward via
        ``core_bridge.evolve()`` to reconstruct the target state.

        Parameters
        ----------
        version_id
            Identifier of the version to reconstruct.
        core_bridge
            Anything exposing ``evolve(structure, patch)`` —
            typically a CoreBridge. Only required when the target
            version (or an intermediate one) is a delta version.

        Raises
        ------
        ValueError
            ``version_id`` does not exist; a delta version was
            reached but no ``core_bridge`` was supplied; session
            history is inconsistent; or a checkpoint's hash
            doesn't match its recorded ``state_hash``.
        """
        index = self._version_index(version_id)
        target_version = self.version_history[index]

        def verify_checkpoint(version: Any, structure: Any) -> None:
            if version.state_hash is None or core_bridge is None or structure is None:
                return
            try:
                actual_hash = core_bridge.hash(structure)
            except (NotImplementedError, RuntimeError):
                return
            if actual_hash != version.state_hash:
                raise ValueError(
                    f"Reconstructed state for version "
                    f"{version.version_id!r} does not match its "
                    f"recorded hash (expected {version.state_hash!r}, "
                    f"got {actual_hash!r})."
                )

        if target_version.is_snapshot:
            verify_checkpoint(target_version, target_version.knowledge_structure)
            return target_version.knowledge_structure

        if core_bridge is None:
            raise ValueError(
                f"Version {version_id!r} was recorded as a delta (no "
                f"stored snapshot) and reconstructing it requires a "
                f"core_bridge capable of evolve() to replay its patch "
                f"chain."
            )

        snapshot_index = index
        while not self.version_history[snapshot_index].is_snapshot:
            snapshot_index -= 1
            if snapshot_index < 0:
                raise ValueError(
                    f"No snapshot found at or before version "
                    f"{version_id!r}; session history is inconsistent "
                    f"(the first version of a session must always be "
                    f"a snapshot)."
                )

        snapshot_version = self.version_history[snapshot_index]
        state = snapshot_version.knowledge_structure
        verify_checkpoint(snapshot_version, state)

        for i in range(snapshot_index + 1, index + 1):
            version = self.version_history[i]
            if version.patch is None:
                raise ValueError(
                    f"Version {version.version_id!r} has neither a "
                    f"stored snapshot nor a recorded patch; cannot "
                    f"reconstruct the chain past it."
                )
            state = core_bridge.evolve(state, version.patch)
            verify_checkpoint(version, state)

        return state

    def _version_index(self, version_id: str) -> int:
        for i, version in enumerate(self.version_history):
            if version.version_id == version_id:
                return i
        raise ValueError(f"Version {version_id!r} not found in session history.")