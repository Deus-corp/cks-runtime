"""
Runtime configuration.

SPEC-001 Runtime Overview.

Contains Runtime-wide configuration that controls
operational behaviour.

Configuration never owns Runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version


def _runtime_version() -> str:
    try:
        return version("cks-runtime")
    except PackageNotFoundError:
        from ._version import __version__
        return __version__


@dataclass(slots=True)
class RuntimeConfig:
    """
    Runtime configuration.

    This object contains only Runtime-wide options.

    Future Runtime specifications may extend it with
    persistence, execution and telemetry settings.
    """

    runtime_name: str = "CKS Runtime"
    runtime_version: str = _runtime_version()
    auto_version_on_commit: bool = True
    collect_runtime_diagnostics: bool = True
    storage_path: str = ":memory:"
    # Session garbage collection.
    # Set gc_retention=None to disable GC entirely.
    gc_retention: timedelta | None = field(default_factory=lambda: timedelta(hours=24))
    gc_sweep_interval: float = 600.0   # seconds between sweeps (default 10 min)
    gc_batch_size: int = 100           # max sessions evicted per sweep
    # Inference staleness sweeping (ADR-009): background re-check of
    # recently-modified sessions for InferenceConfidenceConflict /
    # StalePremise diagnostics (cks-core ADR-001/ADR-002).
    # Set inference_sweep_interval=None to disable the sweeper entirely.
    inference_sweep_interval: float | None = 300.0   # seconds between sweeps (default 5 min)
    inference_sweep_batch_size: int = 100             # initial page size per sweep
    # Provenance staleness sweeping (ADR-010): background re-check of
    # VerificationRecords whose `checked_at` has exceeded a TTL, escalated
    # as `provenance_conflict` outbox tasks for cks-mcp's critic_agent.
    # Set provenance_sweep_interval=None to disable the sweeper entirely.
    provenance_sweep_interval: float | None = 3600.0   # seconds between sweeps (default 1 hour)
    provenance_ttl_seconds: int = 30 * 24 * 3600        # 30 days before a record is stale
    # Temporal staleness sweeping (ADR-011): background re-check of objects
    # whose `valid_until` has passed (cks-core ADR-003,
    # TemporalValidityConstraint), escalated as `temporal_conflict` outbox
    # tasks for cks-mcp's critic_agent.
    # Set temporal_sweep_interval=None to disable the sweeper entirely.
    temporal_sweep_interval: float | None = 3600.0     # seconds between sweeps (default 1 hour)
    temporal_sweep_batch_size: int = 100                # initial page size per sweep
    # Graph freshness sweeping (Memory Agent v2): background re-check of
    # registered graphs (graph_registry, Memory Agent v1) whose `updated_at`
    # has exceeded a TTL, escalated as `graph_outdated` outbox tasks for a
    # future cks-mcp update agent.
    # Set graph_freshness_interval=None to disable the sweeper entirely.
    graph_freshness_interval: float | None = 3600.0     # seconds between sweeps (default 1 hour)
    graph_freshness_ttl_seconds: int = 7 * 24 * 3600     # 7 days before a graph is outdated
    # Graph auto-update sweeping: background cross-check of each
    # registered graph's `Component` objects' recorded `version` against
    # the real `__version__` published in the matching GitHub repository
    # (the same check cks-mcp's `check_component_versions` performs on
    # demand), escalated as `graph_outdated` outbox tasks for a future
    # cks-mcp update agent (or `update_registered_graph` directly, once
    # this sweeper is wired to call it -- see GraphAutoUpdateSweeper's
    # module docstring).
    # Set graph_auto_update_interval=None to disable the sweeper entirely.
    graph_auto_update_interval: float | None = 3600.0    # seconds between sweeps (default 1 hour)
    # If True, ask for outdated components to be applied automatically
    # rather than only surfaced for review. Safe default is False:
    # detect and escalate only. See GraphAutoUpdateSweeper's module
    # docstring for the current (escalation-only) scope of this flag.
    graph_auto_update_apply: bool = False
    # Contradiction detection sweeping: background re-check of recently-
    # modified sessions for MutualExclusionRule/FunctionalRelationRule
    # violations (cks-core's opt-in mutual_exclusion/functional_relation
    # extension constraints), escalated as `contradiction_detected` outbox
    # tasks for cks-mcp's critic_agent.
    # Set contradiction_sweep_interval=None to disable the sweeper entirely.
    contradiction_sweep_interval: float | None = 3600.0  # seconds between sweeps (default 1 hour)
    contradiction_sweep_batch_size: int = 100            # initial page size per sweep