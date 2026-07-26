"""
Runtime ↔ Core semantic contract.

Runtime depends exclusively on this interface.

Concrete semantic engines are supplied through
Runtime plugins.

This interface defines the complete semantic boundary
between Runtime and any Core implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class CoreInterface(ABC):
    """
    Runtime semantic interface.

    Runtime owns:

        • execution
        • orchestration
        • transactions
        • sessions
        • versions

    Core implementations own:

        • semantic validation
        • semantic evolution
        • serialization
        • semantic explanation
    """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @abstractmethod
    def validate(
        self,
        knowledge_structure: Any,
        *,
        extra_constraints: Any = None,
    ) -> RuntimeValidationResult:
        """
        Validate a Knowledge Structure.

        Parameters
        ----------
        extra_constraints
            Optional, Core-implementation-defined extra validation
            rules to apply for this call only (e.g. an opt-in
            extension constraint). A Core implementation that has no
            notion of scoped/extra constraints may ignore this
            parameter; Runtime never inspects or interprets it, it is
            passed through verbatim.

        Returns
        -------
        RuntimeValidationResult
            Canonical Runtime validation model.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    @abstractmethod
    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        """
        Apply one semantic evolution.

        Parameters
        ----------
        knowledge_structure
            Current Knowledge Structure.

        operation
            Canonical evolution description.

        Returns
        -------
        Any
            Resulting Knowledge Structure.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @abstractmethod
    def serialize(
        self,
        knowledge_structure: Any,
    ) -> str:
        """
        Produce canonical serialization.

        Returns
        -------
        str
            Canonical serialized representation.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Explainability
    # ------------------------------------------------------------------

    @abstractmethod
    def explain(
        self,
        knowledge_structure: Any,
    ) -> dict[str, Any]:
        """
        Produce a semantic explanation.

        Returns
        -------
        dict[str, Any]
            Structured semantic explanation.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Structural Diff
    # ------------------------------------------------------------------

    @abstractmethod
    def diff(self, source: Any, target: Any) -> list[Any]:
        """Compute structural diff between two Core-native structures."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Field-level structural diff (optional capability)
    # ------------------------------------------------------------------

    def field_diff(self, source: Any, target: Any) -> list[Any]:
        """
        Compute a field-granular structural diff between two
        Core-native structures.

        This is an *optional* capability, like ``merge()``/
        ``query_subgraph()``/``hash()``: Runtime must not assume every
        plugged-in Core can report which fields changed inside a
        modified identity. ``diff()`` above is the only diff every
        Core must support, and it is free to report any content
        change as a whole-object remove+add -- exactly what cks-core's
        own ``diff()`` does, so that ``cks.evolution.compose`` never
        has to reason about partial objects. Core implementations
        that can do better should override this method.

        Returns
        -------
        list[cks_runtime.core_api.field_operation.RuntimeFieldOperation]
            Field-granular changes. Unlike ``merge()``/
            ``query_subgraph()``, whose results Runtime treats as
            fully Core-opaque, this crosses the boundary as the one
            Runtime-native shape (mirroring ``RuntimeMergeConflict``):
            Runtime's operation log (ADR-007) needs a stable shape to
            persist, regardless of which Core produced the diff.

        Raises
        ------
        NotImplementedError
            The attached Core does not support field-level diffing.
            Propagated as-is, matching ``hash()``/``merge()``'s
            contract, so callers can distinguish "not supported" from
            "supported but nothing changed" (an empty list).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement field_diff()."
        )

    # ------------------------------------------------------------------
    # Field-level merge synthesis (optional capability)
    # ------------------------------------------------------------------

    def synthesize_merge(self, base_object: Any, operations: list[Any]) -> Any:
        """
        Apply a set of non-conflicting field-level operations
        (``RuntimeFieldOperation`` instances, all ``set_field``/
        ``delete_field``) on top of ``base_object``, producing the
        Core-native object both branches' changes converge to.

        The write-side counterpart to ``field_diff()`` -- tied to the
        same optional capability, since a Core that cannot report
        which fields changed has no basis for reconstructing them
        either. A Core implementation that overrides ``field_diff()``
        should also override this method.

        Raises
        ------
        NotImplementedError
            The attached Core does not support field-level merge
            synthesis. Propagated as-is, matching ``field_diff()``'s
            contract.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement synthesize_merge()."
        )

    # ------------------------------------------------------------------
    # Three-way merge (optional capability)
    # ------------------------------------------------------------------

    def merge(
        self,
        base: Any,
        branch_a: Any,
        branch_b: Any,
        *,
        resolutions: dict[str, Any] | None = None,
    ) -> Any:
        """
        Three-way merge two independently evolved structures against
        their common ancestor.

        This is an *optional* capability, like ``hash()``: Runtime
        must not assume every plugged-in Core supports branching and
        merging. Core implementations that can provide it should
        override this method; callers must be prepared to catch
        ``NotImplementedError`` for a Core that doesn't.

        Parameters
        ----------
        base
            The common ancestor (lowest common ancestor) structure.
        branch_a, branch_b
            Two structures assumed to have evolved from ``base``.
        resolutions
            Optional per-identity conflict resolution, keyed by
            object id, turning this into a *partial* merge: a
            conflicting identity with an entry here is resolved using
            that entry instead of blocking the merge; only identities
            still unresolved after this raise. Core implementations
            that don't support this MAY ignore it and merge as if it
            were empty -- callers that need it should treat a merge
            result which still reports a resolved id as a conflict as
            a sign the attached Core doesn't honor it, not assume
            every Core does.

        Returns
        -------
        Any
            The merged Core-native structure.

        Raises
        ------
        RuntimeMergeConflictError
            ``branch_a`` and ``branch_b`` changed one or more of the
            same identities to different, irreconcilable results, and
            (if ``resolutions`` was given) at least one has no entry
            there. Core implementations should raise this
            Runtime-native error (translating their own conflict
            representation into it) rather than an
            implementation-specific exception, so callers never need
            to know which Core is attached.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement merge()."
        )

    # ------------------------------------------------------------------
    # Subgraph query (optional capability)
    # ------------------------------------------------------------------

    def query_subgraph(
        self,
        knowledge_structure: Any,
        seed_ids: Any,
        depth: int = 1,
        *,
        include_relation_types: Any = None,
        include_object_types: Any = None,
        max_tokens: int | None = None,
        max_objects: int | None = None,
        type_weights: Any = None,
    ) -> Any:
        """
        Extract the local k-hop neighborhood around ``seed_ids`` from
        ``knowledge_structure``, as a self-contained subgraph.

        Optional, like ``merge()``/``hash()``: Runtime must not assume
        every plugged-in Core exposes graph traversal. Core
        implementations that can provide it should override this
        method; callers must be prepared to catch
        ``NotImplementedError`` for a Core that doesn't.

        The return value is treated as fully opaque by Runtime -- the
        same way ``knowledge_structure`` itself already is -- since
        it's just a Core-native result (e.g. cks-core's
        ``SubgraphResult``) carrying a Core-native structure alongside
        whatever truncation metadata that Core implementation
        produces. Runtime never inspects its fields, unlike the
        conflict-carrying exception ``merge()`` raises, which does
        need a Runtime-native shape because it crosses the boundary as
        control flow rather than as a plain return value.

        Parameters mirror cks-core's own
        ``KnowledgeStructure.query_subgraph`` (depth, per-type
        filters, and an optional token/object budget with
        type-weighted ranking) but are typed ``Any`` here since
        CoreInterface must not assume any particular Core's filter or
        weight representation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement query_subgraph()."
        )

    # ------------------------------------------------------------------
    # Content hashing (optional capability)
    # ------------------------------------------------------------------

    def hash(self, knowledge_structure: Any) -> str:
        """
        Return a stable content hash for a Core-native structure.

        This is an *optional* capability, unlike the methods above:
        Runtime must not assume every plugged-in Core supports
        content-addressable hashing (see the copy-semantics note in
        ``RuntimeVersion``). Core implementations that can provide a
        stable hash should override this method; callers that rely on
        it (e.g. history reconstruction/integrity checks) must be
        prepared to catch ``NotImplementedError`` and fall back to
        not verifying integrity, rather than assuming this is always
        available.

        Returns
        -------
        str
            A stable content hash, equal for two structures the Core
            implementation considers structurally equivalent.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement hash()."
        )