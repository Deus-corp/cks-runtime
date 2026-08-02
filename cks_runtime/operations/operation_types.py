"""
Canonical Runtime Operations.
"""

from __future__ import annotations

import logging
from typing import Any

from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    Operation,
    OperationStatus,
)
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_vector import VersionVector

logger = logging.getLogger(__name__)


class ValidateOperation(Operation):
    """Validate a Knowledge Structure."""
    operation_id: str = "validate"

    def __init__(
        self,
        operation_id: str = "validate",
        *,
        knowledge_structure: Any = None,
        extra_constraints: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure
        self.extra_constraints = extra_constraints

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        result = executor.core.validate(
            self.knowledge_structure,
            extra_constraints=self.extra_constraints,
        )
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED if result.valid else OperationStatus.FAILED,
            payload=result,
            diagnostics=result.diagnostics,
            error=None if result.valid else RuntimeError("Validation failed"),
        )


class EvolveOperation(Operation):
    """Apply a semantic evolution."""
    operation_id: str = "evolve"

    def __init__(
        self,
        operation_id: str = "evolve",
        *,
        knowledge_structure: Any = None,
        evolution: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure
        self.evolution = evolution

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        evolved = executor.core.evolve(self.knowledge_structure, self.evolution)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=evolved,
        )


class SerializeOperation(Operation):
    """Serialize a Knowledge Structure."""
    operation_id: str = "serialize"

    def __init__(
        self,
        operation_id: str = "serialize",
        *,
        knowledge_structure: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        serialized = executor.core.serialize(self.knowledge_structure)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=serialized,
        )


class ExplainOperation(Operation):
    """Produce a semantic explanation."""
    operation_id: str = "explain"

    def __init__(
        self,
        operation_id: str = "explain",
        *,
        knowledge_structure: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        explanation = executor.core.explain(self.knowledge_structure)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=explanation,
        )


class QuerySubgraphOperation(Operation):
    """
    Extract the local k-hop neighborhood around one or more seed ids
    from a Knowledge Structure, as a self-contained subgraph.

    Read-only, like ``ExplainOperation``: it never mutates session
    state, so unlike ``EvolveOperation``/``RevertVersionOperation``/
    ``MergeOperation`` it is deliberately NOT special-cased in
    ``ExecutionPipeline._apply_state_mutation`` -- committing it
    through a transaction records a version whose Knowledge Structure
    is unchanged from before the operation ran, the same as
    committing a bare ``ExplainOperation`` does today.
    """
    operation_id: str = "query_subgraph"

    def __init__(
        self,
        operation_id: str = "query_subgraph",
        *,
        knowledge_structure: Any = None,
        seed_ids: Any = None,
        depth: int = 1,
        include_relation_types: Any = None,
        include_object_types: Any = None,
        max_tokens: int | None = None,
        max_objects: int | None = None,
        type_weights: Any = None,
        compact_mode: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure
        self.seed_ids = seed_ids
        self.depth = depth
        self.include_relation_types = include_relation_types
        self.include_object_types = include_object_types
        self.max_tokens = max_tokens
        self.max_objects = max_objects
        self.type_weights = type_weights
        self.compact_mode = compact_mode

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        if self.seed_ids is None:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=ValueError(
                    "QuerySubgraphOperation requires 'seed_ids'."
                ),
            )

        try:
            result = executor.core.query_subgraph(
                self.knowledge_structure,
                self.seed_ids,
                self.depth,
                include_relation_types=self.include_relation_types,
                include_object_types=self.include_object_types,
                max_tokens=self.max_tokens,
                max_objects=self.max_objects,
                type_weights=self.type_weights,
            )
        except Exception as exc:  # noqa: BLE001 -- Core plugin boundary; captured below, not swallowed
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=result,
        )


class ListVersionsOperation(Operation):
    """List all versions in the current session history."""
    operation_id: str = "list_versions"

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        versions_data = [
            {
                "version_id": v.version_id,
                "created_at": v.created_at.isoformat(),
                "transaction_id": v.transaction_id,
                "metadata": dict(v.metadata),
            }
            for v in session.version_history
        ]
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=versions_data,
        )


class RevertVersionOperation(Operation):
    """Revert the Knowledge Structure to a specific previous version."""
    operation_id: str = "revert_version"

    def __init__(
        self,
        operation_id: str = "revert_version",
        *,
        target_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.target_version_id = target_version_id

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        try:
            target_structure = session.get_version_state(
                self.target_version_id,
                executor.core,
            )
        except ValueError as exc:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=target_structure,
        )


class DiffOperation(Operation):
    """Compute structural delta between current session and a target state/version."""
    operation_id: str = "diff"

    def __init__(
        self,
        operation_id: str = "diff",
        *,
        target_version_id: str | None = None,
        target_structure: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.target_version_id = target_version_id
        self.target_structure = target_structure

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        if self.target_version_id is not None:
            try:
                target = session.get_version_state(
                    self.target_version_id,
                    executor.core,
                )
            except ValueError as exc:
                return ExecutionResult(
                    operation_id=self.operation_id,
                    status=OperationStatus.FAILED,
                    error=exc,
                )
        elif self.target_structure is not None:
            target = self.target_structure
        else:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=ValueError(
                    "DiffOperation requires either 'target_version_id' or "
                    "'target_structure'."
                ),
            )

        try:
            diff_patch = executor.core.diff(
                source=session.knowledge_structure,
                target=target,
            )
        except Exception as e:  # noqa: BLE001 -- Core plugin boundary; captured below, not swallowed
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=e,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=diff_patch,
        )


EMPTY_STATE_VERSION_ID = "00000000-0000-0000-0000-000000000000"
"""
Well-known, deterministic version id representing "empty state, no
real history" -- the same trick as git's empty-tree hash
(``4b825dc642cb6eb9a060e54bf8d69288fbee4904``), used there as a diff
base for a repository's very first commit, which likewise has no
real parent to diff against.

A session's ``parent_version_id`` pointing at this constant means
"this lineage's recorded fork point is the empty structure" rather
than "no fork point is recorded at all" (``None``). ``MergeOperation``
resolves it directly to an empty structure of the right type (see
below) without a ``version_history`` lookup, so it needs no entry to
actually exist in *any* session's history, on *any* replica -- every
replica reconstructs the same empty state locally and independently.

This is what lets two sessions that were bootstrapped on different
processes with no shared storage still name a common ancestor for a
three-way merge: any two ``RuntimeSession``s whose ``parent_version_id``
is this constant are defined to share it, without either having ever
seen the other's actual version history. Used by gossip's
first-contact bootstrap (``GossipAdapter._bootstrap_remote_session``,
``GossipAdapter.anchor_genesis``, ADR-008) for exactly that reason,
but the constant and the short-circuit in ``MergeOperation`` are
plain versioning-layer mechanism, not gossip-specific -- ADR-007
``create_branch``/``merge_branch`` callers are free to use it too for
a from-scratch branch with no real parent.
"""


class MergeOperation(Operation):
    """
    Three-way merge of another (source) session's branch into the
    current session.

    The merge base (lowest common ancestor) can be supplied directly
    via ``base_structure``, or resolved from a version id in the
    *current* session's own history via ``base_version_id``. When
    neither is given, ``source_session.parent_version_id`` is used --
    the common case of merging a branch back into the session it
    forked from, where the branch itself recorded its own fork point
    at creation time (see ``SessionManager.create_branch``).

    On conflict, ``executor.core.merge()`` raises
    ``RuntimeMergeConflictError`` (with a ``.conflicts`` list), which
    this operation captures as ``ExecutionResult.error`` without
    raising it further. Callers that need the structured conflict
    list -- e.g. to present it to an LLM agent for manual resolution
    -- should run this operation directly via
    ``executor.execute(MergeOperation(...), session)`` and inspect
    the result before deciding whether to commit a transaction.
    Going through ``Runtime.commit_transaction`` instead re-raises any
    failure as a generic ``RuntimeError`` (see
    ``ExecutionPipeline._handle_result``), which loses the structured
    conflict list.
    """
    operation_id: str = "merge"

    def __init__(
        self,
        operation_id: str = "merge",
        *,
        source_session: RuntimeSession | None = None,
        base_version_id: str | None = None,
        base_structure: Any = None,
        resolutions: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.source_session = source_session
        self.base_version_id = base_version_id
        self.base_structure = base_structure
        self.resolutions = resolutions

    async def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        if self.source_session is None:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=ValueError(
                    "MergeOperation requires 'source_session' (the "
                    "branch being merged in)."
                ),
            )

        # ADR-007 Part 2: compare version vectors for fast-path decisions
        # before resolving the merge base.
        target_vector = VersionVector.from_metadata(session.metadata)
        source_vector = VersionVector.from_metadata(self.source_session.metadata)

        if target_vector.dominates(source_vector):
            # Target already contains everything source has — no-op.
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.COMPLETED,
                payload=session.knowledge_structure,
            )

        if source_vector.dominates(target_vector):
            # Source is a strict descendant — fast-forward.
            target_vector.absorb(source_vector)
            target_vector.to_metadata(session.metadata)
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.COMPLETED,
                payload=self.source_session.knowledge_structure,
            )

        base_version_id: str | None = None

        if self.base_structure is not None:
            base = self.base_structure
        else:
            base_version_id = (
                self.base_version_id
                if self.base_version_id is not None
                else self.source_session.parent_version_id
            )
            if base_version_id is None:
                return ExecutionResult(
                    operation_id=self.operation_id,
                    status=OperationStatus.FAILED,
                    error=ValueError(
                        "MergeOperation could not determine a merge "
                        "base: pass 'base_structure' or "
                        "'base_version_id' explicitly, or merge a "
                        "session whose 'parent_version_id' was "
                        "recorded at branch time."
                    ),
                )
            if base_version_id == EMPTY_STATE_VERSION_ID:
                # No real history to walk -- every replica reconstructs
                # this independently, so no get_version_state() lookup
                # (and no requirement that this id appear in anyone's
                # version_history) is needed. See EMPTY_STATE_VERSION_ID.
                base = type(session.knowledge_structure)([])
            else:
                try:
                    base = session.get_version_state(
                        base_version_id,
                        executor.core,
                    )
                except ValueError as exc:
                    return ExecutionResult(
                        operation_id=self.operation_id,
                        status=OperationStatus.FAILED,
                        error=exc,
                    )

        try:
            merged = executor.core.merge(
                base,
                session.knowledge_structure,
                self.source_session.knowledge_structure,
                resolutions=self.resolutions,
            )
        except RuntimeMergeConflictError as exc:
            # ADR-007 fast path: before surfacing the conflict, check
            # whether the operation log shows both branches only
            # touched disjoint structure keys on the conflicting
            # identities -- if so, synthesize resolutions for those
            # and retry once. Explicit self.resolutions still wins
            # over an auto-computed one for the same id.
            auto = await self._field_level_resolutions(
                exc.conflicts, session, base_version_id, executor
            )
            if not auto:
                return ExecutionResult(
                    operation_id=self.operation_id,
                    status=OperationStatus.FAILED,
                    error=exc,
                )
            try:
                merged = executor.core.merge(
                    base,
                    session.knowledge_structure,
                    self.source_session.knowledge_structure,
                    resolutions={**auto, **(self.resolutions or {})},
                )
            except Exception as exc2:  # noqa: BLE001 -- Core plugin boundary; captured below, not swallowed
                return ExecutionResult(
                    operation_id=self.operation_id,
                    status=OperationStatus.FAILED,
                    error=exc2,
                )
        except Exception as exc:  # noqa: BLE001 -- Core plugin boundary; captured below, not swallowed
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=merged,
        )

    async def _field_level_resolutions(
        self,
        conflicts: list[Any],
        session: RuntimeSession,
        base_version_id: str | None,
        executor,
    ) -> dict[str, Any]:
        storage = getattr(executor, "storage", None)
        core = executor.core

        # Only reachable from execute() after its own
        # `self.source_session is None` guard above, so this always
        # holds -- this just makes the invariant visible to mypy.
        assert self.source_session is not None

        if (
            base_version_id is None
            or storage is None
            or not storage.supports_operation_log
            or not core.supports_synthesize_merge
        ):
            return {}

        def versions_since(rt_session: RuntimeSession) -> set[str] | None:
            version_ids = [v.version_id for v in rt_session.version_history]
            if base_version_id in version_ids:
                return set(version_ids[version_ids.index(base_version_id) + 1 :])
            if rt_session.parent_version_id == base_version_id:
                return set(version_ids)
            return None

        a_versions = versions_since(session)
        b_versions = versions_since(self.source_session)
        if a_versions is None or b_versions is None:
            return {}

        resolutions: dict[str, Any] = {}
        for conflict in conflicts:
            oid = conflict.object_id
            if conflict.base is None:
                continue

            a_ops = [
                op
                for op in await storage.list_operations(session.session_id, object_id=oid)
                if op.version_id in a_versions
            ]
            b_ops = [
                op
                for op in await storage.list_operations(
                    self.source_session.session_id, object_id=oid
                )
                if op.version_id in b_versions
            ]

            if not a_ops and not b_ops:
                continue
            if any(
                op.op_type not in ("set_field", "delete_field")
                for op in a_ops + b_ops
            ):
                continue
            if {op.field_key for op in a_ops} & {op.field_key for op in b_ops}:
                continue

            try:
                resolutions[oid] = core.synthesize_merge(conflict.base, a_ops + b_ops)
            except Exception:
                logger.warning(
                    "ADR-007 auto-resolution failed for object %s; "
                    "falling back to the original conflict.",
                    oid,
                    exc_info=True,
                )
                continue

        return resolutions