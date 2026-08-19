"""
Runtime Session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def _dedupe_replayed_add_objects(state: Any, patch: Any) -> Any:
    """
    Filter a stored patch (about to be replayed via
    ``core_bridge.evolve``) so that an ``AddObject`` whose target id
    already exists in ``state`` never reaches ``cks.evolution.compose``.

    This is specifically about *replaying a historical patch during
    version reconstruction* (``get_version_state``), not general
    evolution: ``cks.evolution.AddObject._mutate`` raises a bare
    ``ValueError('Object ... already exists.')`` when an id collides,
    which is the right behavior for a live edit (see
    ``fork_sandbox``/``evolve_knowledge`` -- a caller-supplied
    ``AddObject`` for an id that already exists is a genuine error to
    report, not something to silently paper over). Reconstruction is
    different: replaying a stored patch chain can reapply an
    ``AddObject`` for an id introduced earlier in the same chain, or
    already present in the snapshot the chain is replayed on top of,
    whenever the version being reconstructed shares object ids with
    the structure the patch was originally captured against. Scoping
    this dedupe to only the reconstruction replay loop (rather than
    ``CksCoreAdapter.evolve`` generally) keeps that distinction
    intact.

    Two cases when a collision is found, so genuine corruption isn't
    silently hidden:

    * The existing object is identical (same identity + structure) to
      the one ``AddObject`` would add: a true no-op replay -- drop the
      operator.
    * The existing object differs: a real conflict, not a replay
      artefact. Logged as a warning and dropped rather than applied,
      since blindly overwriting could discard data, and blindly
      raising would crash reconstruction entirely (the original bug).

    Non-``AddObject`` operators (including ``RemoveObject``, tracked
    here so a same-batch remove-then-add for the same id -- the
    pattern cks-core's own diff produces for any "replace this
    object's identity" edit -- still lets the add through) pass
    through unchanged. If ``patch`` isn't a plain list/tuple of
    operators, or contains no ``AddObject`` at all, it's returned
    untouched.
    """
    if not isinstance(patch, (list, tuple)):
        return patch

    from cks.evolution import AddObject, RemoveObject

    if not any(isinstance(op, AddObject) for op in patch):
        return patch

    existing = {obj.identity.id: obj for obj in state.objects}
    filtered: list[Any] = []
    for op in patch:
        if isinstance(op, RemoveObject):
            existing.pop(op.object_id, None)
            filtered.append(op)
            continue
        if isinstance(op, AddObject) and op.obj.identity.id in existing:
            current = existing[op.obj.identity.id]
            if current == op.obj:
                logger.warning(
                    "Reconstruction replay: AddObject for '%s' targets "
                    "an object that already exists and is identical; "
                    "treating as a no-op.",
                    op.obj.identity.id,
                )
            else:
                logger.warning(
                    "Reconstruction replay: AddObject for '%s' targets "
                    "an object that already exists with different "
                    "content; skipping to avoid crashing reconstruction. "
                    "This may indicate a genuine data conflict and "
                    "should be investigated.",
                    op.obj.identity.id,
                )
            continue
        filtered.append(op)
        if isinstance(op, AddObject):
            existing[op.obj.identity.id] = op.obj
    return filtered


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

    #: Identifier of the RuntimeSession this session branched from, or
    #: ``None`` for a root session created directly from a Knowledge
    #: Structure (not via ``SessionManager.create_branch``).
    parent_session_id: str | None = None

    #: Identifier of the specific version of the parent session this
    #: branch forked from. ``None`` means the branch started from the
    #: parent's live (uncommitted or latest) state at branch time
    #: rather than a recorded historical version -- callers that need
    #: an exact fork point for a later merge should pass an explicit
    #: version when branching.
    parent_version_id: str | None = None

    def __post_init__(self) -> None:
        # VersionManager.create() computes `index % snapshot_interval`
        # to decide whether a version is a snapshot or a delta. A
        # value <= 0 doesn't raise here -- it raises later, as a bare
        # ZeroDivisionError (0) or as version indices that never land
        # on a snapshot boundary (negative), on whatever commit
        # happens to trip it. Failing fast at construction gives a
        # clear error at the point the bad value was actually set.
        if self.snapshot_interval <= 0:
            raise ValueError(
                f"snapshot_interval must be >= 1, got {self.snapshot_interval!r}."
            )

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

    @property
    def is_branch(self) -> bool:
        """Whether this session was created as a branch of another session."""
        return self.parent_session_id is not None

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
            patch = _dedupe_replayed_add_objects(state, version.patch)
            state = core_bridge.evolve(state, patch)
            verify_checkpoint(version, state)

        return state

    def _version_index(self, version_id: str) -> int:
        for i, version in enumerate(self.version_history):
            if version.version_id == version_id:
                return i
        raise ValueError(f"Version {version_id!r} not found in session history.")