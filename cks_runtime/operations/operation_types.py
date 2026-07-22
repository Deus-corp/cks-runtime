"""
Canonical Runtime Operations.
"""

from __future__ import annotations
from typing import Any
from cks_runtime.execution.operation_executor import (
    Operation,
    ExecutionResult,
    OperationStatus,
)
from cks_runtime.session.session import RuntimeSession


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

    def execute(
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

    def execute(
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

    def execute(
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

    def execute(
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

    def execute(
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
        except Exception as exc:
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

    def execute(
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

    def execute(
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

    def execute(
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
        except Exception as e:
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.source_session = source_session
        self.base_version_id = base_version_id
        self.base_structure = base_structure

    def execute(
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
            )
        except Exception as exc:
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