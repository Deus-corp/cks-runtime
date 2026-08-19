"""
GraphAutoUpdateSweeper: background detection of registered graphs
whose `Component` objects have drifted out of date with the real
code they describe.

Structural counterpart to GraphFreshnessSweeper. Where that sweeper
flags a graph purely because its `updated_at` timestamp is old (a
proxy for staleness), this one actually cross-checks the `version`
field recorded on each `Component` object in the graph's session
against the real `__version__` published in that component's GitHub
repository -- the same check cks-mcp's `check_component_versions`
tool performs on demand, run here automatically on a schedule.

This sweeper is detection (and, only when explicitly opted into,
escalation-time) only. It does not itself rewrite graph content --
that stays in cks-mcp's `update_registered_graph`, which needs an
LLM provider (`construct_knowledge`) to turn "component X went from
1.2.0 to 1.3.0" into proper CKS objects, consistent with Runtime
never originating decisions or holding LLM/provider configuration
(see ADR-001, Runtime Layering; ADR-010 makes the same choice for
provenance re-verification, and GraphFreshnessSweeper for graph
staleness generally).

Because of that, `RuntimeConfig.graph_auto_update_apply` currently
has exactly one effect: it is recorded on the escalated outbox
payload as `auto_apply_requested`, so a future cks-mcp consumer of
`graph_outdated` tasks can tell "the operator asked for this to be
applied automatically" apart from "this was only ever meant to be
surfaced for review" -- actually calling `update_registered_graph`
from here would mean this sweeper becomes an HTTP client of the
MCP server (a new, separate network surface, undertaking its own
auth/session story) purely to re-enter cks-mcp code that already
runs in-process there. That wiring is left for a follow-up once
there's a real MCP-server-to-itself calling convention to hang it
off of; until then, `graph_auto_update_apply=True` still only
escalates, exactly like the default `False` case.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from cks_runtime.net.safe_fetch import UnsafeURLError, safe_get
from cks_runtime.reasoning.sweeper_status import SweeperStatusMixin

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

DEFAULT_SWEEP_INTERVAL_SECONDS = 3600  # hourly, matches GraphFreshnessSweeper

# task_type value written to cks_outbox_tasks -- same task_type as
# GraphFreshnessSweeper (both describe "this registered graph needs
# attention"); the `reason` field in the payload distinguishes them.
_GRAPH_OUTDATED_TASK_TYPE = "graph_outdated"

_COMPONENT_TYPE = "Component"

_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")
_DEFAULT_BRANCH = "main"

# Keep in sync with cks-mcp's check_component_versions._KNOWN_COMPONENTS.
# Runtime must not import from cks-mcp (cks-mcp depends on cks-runtime,
# never the reverse -- ADR-001, Runtime Layering), so this map is
# duplicated here rather than shared.
_KNOWN_COMPONENTS: dict[str, dict[str, str]] = {
    "cks-core": {"repo": "punctumactus/cks-core", "path": "src/cks/_version.py"},
    "cks-runtime": {"repo": "punctumactus/cks-runtime", "path": "src/cks_runtime/_version.py"},
    "cks-mcp": {"repo": "punctumactus/cks-mcp", "path": "src/cks_mcp/_version.py"},
}

# Candidate _version.py locations tried, in order, for a component
# whose repository is known only via a 'repo_url' field on the
# Component object (not one of the hard-coded ecosystem repos above).
_CANDIDATE_PATHS = (
    "_version.py",
    "{pkg}/_version.py",
    "src/{pkg}/_version.py",
)

# Candidate package.json locations tried, in order, for a JS/TS
# component (identified via 'version_source': 'package.json' on the
# Component object's structure, same convention as cks-mcp's
# check_component_versions). Most components keep it at the repo
# root; a handful of monorepo-style layouts nest it under a package
# dir sharing the component's name.
_PACKAGE_JSON_CANDIDATE_PATHS = (
    "package.json",
    "{pkg}/package.json",
)


def _repo_from_url(repo_url: str) -> str | None:
    """Resolve a component's declared 'repo_url' to an 'owner/repo'
    string. Accepts a full GitHub URL (e.g.
    'https://github.com/punctumactus/cks-runtime', with or without a
    trailing '.git' or path suffix) as well as a bare 'owner/repo'
    string (e.g. 'punctumactus/cks-runtime'), since some callers/tests
    and older stored graph data supply the short form directly rather
    than a full URL."""
    parsed = urlparse(repo_url)
    if parsed.netloc:
        if "github.com" not in parsed.netloc:
            return None
        parts = [p for p in parsed.path.split("/") if p]
    else:
        # No scheme/netloc -- treat as a bare "owner/repo" (or
        # "owner/repo.git") string.
        parts = [p for p in repo_url.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    return f"{owner}/{repo.removesuffix('.git')}"


def _pkg_name(component_name: str) -> str:
    return component_name.replace("-", "_")


def _raw_url(repo: str, path: str, branch: str = _DEFAULT_BRANCH) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def _resolve_component(
    component_name: str, structure: dict[str, Any]
) -> tuple[str | None, tuple[str, ...], str]:
    """
    Work out which GitHub repo (and which version-file paths to try in
    it) a Component object corresponds to.

    Returns ``(repo, candidate_paths, source)``; ``repo`` is ``None``
    if it couldn't be determined at all. ``source`` is
    ``"package_json"`` when the Component's structure declares
    ``'version_source': 'package.json'`` (JS/TS components, which have
    no ``_version.py`` to find), else ``"python"`` (the existing
    ``__version__``-in-``_version.py`` convention). Mirrors cks-mcp's
    ``check_component_versions._resolve_component`` -- see the
    `_KNOWN_COMPONENTS` module docstring for why this isn't shared
    directly.
    """
    is_npm = structure.get("version_source") == "package.json"

    known = _KNOWN_COMPONENTS.get(component_name)
    if known is not None and not is_npm:
        return known["repo"], (known["path"],), "python"

    repo_url = structure.get("repo_url")
    if repo_url:
        repo = _repo_from_url(repo_url)
        if repo is not None:
            pkg = _pkg_name(component_name)
            if is_npm:
                paths = tuple(p.format(pkg=pkg) for p in _PACKAGE_JSON_CANDIDATE_PATHS)
                return repo, paths, "package_json"
            return repo, tuple(p.format(pkg=pkg) for p in _CANDIDATE_PATHS), "python"

    if known is not None:
        # npm requested but only a Python-style known mapping exists --
        # fall back to it rather than reporting "no repo", since we do
        # at least know the repo.
        return known["repo"], _PACKAGE_JSON_CANDIDATE_PATHS, "package_json"

    return None, (), "python"


def _fetch_version_sync(repo: str, candidate_paths: tuple[str, ...]) -> tuple[str | None, str | None]:
    """
    Try each candidate path against `repo`'s default branch until one
    resolves to a parsable `__version__`. Returns (version, error)
    where exactly one is None. Blocking -- dispatch via
    asyncio.to_thread.
    """
    last_error: str | None = None
    for path in candidate_paths:
        url = _raw_url(repo, path)
        try:
            resp = safe_get(url)
        except UnsafeURLError as exc:
            return None, f"unsafe_url: {exc}"
        except Exception as exc:  # noqa: BLE001 - network is inherently unreliable
            last_error = f"error fetching {url}: {exc}"
            continue
        if resp is None or resp.status_code != 200:
            last_error = f"could not fetch {url}"
            continue
        match = _VERSION_RE.search(resp.text)
        if not match:
            last_error = f"no __version__ found at {url}"
            continue
        return match.group(1), None
    return None, last_error or f"no candidate paths for {repo}"


def _fetch_package_json_version_sync(
    repo: str, candidate_paths: tuple[str, ...]
) -> tuple[str | None, str | None]:
    """
    Same shape and calling convention as `_fetch_version_sync`, but for
    JS/TS components: fetches `package.json` over the GitHub raw API
    and reads its top-level "version" field instead of parsing a
    Python `__version__` assignment. Mirrors cks-mcp's
    `check_component_versions._fetch_package_json_version_sync`.

    Returns ``(version, error)`` where exactly one of the two is
    ``None``. Blocking -- dispatch via asyncio.to_thread.
    """
    last_error: str | None = None
    for path in candidate_paths:
        url = _raw_url(repo, path)
        try:
            resp = safe_get(url)
        except UnsafeURLError as exc:
            return None, f"unsafe_url: {exc}"
        except Exception as exc:  # noqa: BLE001 - network is inherently unreliable
            last_error = f"error fetching {url}: {exc}"
            continue
        if resp is None or resp.status_code != 200:
            last_error = f"could not fetch {url}"
            continue
        try:
            data = json.loads(resp.text)
        except (ValueError, TypeError):
            last_error = f"invalid JSON at {url}"
            continue
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str) or not version:
            last_error = f"no 'version' field found at {url}"
            continue
        return version, None
    return None, last_error or f"no candidate paths for {repo}"


def _version_tuple(version: Any) -> tuple[int, ...] | None:
    if not isinstance(version, str):
        return None
    version = version.lstrip("v")
    parts = []
    for chunk in version.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            return None
    return tuple(parts)


def _is_outdated(graph_version: Any, actual_version: str) -> bool:
    """
    True if `graph_version` (recorded on the Component object) is
    behind `actual_version` (just fetched from GitHub). Mirrors
    check_component_versions._compare_versions, but collapsed to a
    boolean since this sweeper only needs to know "outdated or not",
    not distinguish "up_to_date" from "ahead".
    """
    if graph_version == actual_version:
        return False

    if isinstance(graph_version, str):
        graph_version = graph_version.lstrip("v")
    if isinstance(actual_version, str):
        actual_version = actual_version.lstrip("v")

    graph_tuple = _version_tuple(graph_version)
    actual_tuple = _version_tuple(actual_version)
    if graph_tuple is not None and actual_tuple is not None:
        return graph_tuple < actual_tuple

    # Unparsable on one side or the other: already know they differ.
    # Same "outdated" default as check_component_versions, since
    # that's the case this tool exists to catch.
    return True


class GraphAutoUpdateSweeper(SweeperStatusMixin):
    """
    Periodically scans every entry in `graph_registry`, loads each
    graph's session, and cross-checks any `Component` objects it
    contains (identified by having a `version` field in their
    `structure`) against the real version published in the matching
    GitHub repository. Escalates a `graph_outdated` outbox task for
    each graph with at least one outdated component.

    Mirrors GraphFreshnessSweeper's constructor shape, lifecycle
    (start/stop as an asyncio background task), dedup strategy
    (`_known_stale`), and no-op-on-unsupported-storage behaviour.

    Parameters
    ----------
    storage:
        The runtime storage backend. No-op when it doesn't support
        the outbox (`supports_outbox` False), e.g. `InMemoryStorage`.
    interval_seconds:
        How often the sweep loop wakes up. Defaults to 1 hour.
    apply_updates:
        Mirrors `RuntimeConfig.graph_auto_update_apply`. Currently
        only changes the escalated payload's `auto_apply_requested`
        field -- see the module docstring for why actually applying
        the update isn't wired up here yet.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        apply_updates: bool = False,
    ) -> None:
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._apply_updates = apply_updates
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # graph name -> already escalated as outdated on a prior sweep.
        # Same rationale as GraphFreshnessSweeper._known_stale: without
        # this, a graph stays outdated for many sweep intervals in a
        # row and would otherwise get a fresh outbox task every single
        # time. Cleared for a graph once it stops appearing in
        # `current_outdated` (e.g. re-registered with fixed versions),
        # so a later regression is escalated again.
        self._known_stale: set[str] = set()

        self._init_sweeper_status()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        async with self._control_lock:
            if self._running:
                return
            if not getattr(self._storage, "supports_outbox", False):
                logger.info(
                    "Storage backend does not support outbox; "
                    "GraphAutoUpdateSweeper will not start."
                )
                return
            self._running = True
            self._task = asyncio.create_task(self._run(), name="cks-graph-auto-update-sweep")
            logger.info(
                "GraphAutoUpdateSweeper started (interval=%ds, apply_updates=%s).",
                self._interval_seconds,
                self._apply_updates,
            )

    async def stop(self) -> None:
        async with self._control_lock:
            self._running = False
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
            logger.info("GraphAutoUpdateSweeper stopped.")

    # ------------------------------------------------------------------
    # Sweep loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            started_at = datetime.now(UTC)
            try:
                result = await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_sweep_error(started_at, exc)
                logger.exception(
                    "GraphAutoUpdateSweeper sweep failed; will retry next interval."
                )
            else:
                self._record_sweep_success(started_at, result)
            await asyncio.sleep(self._interval_seconds)
            desired = self._storage.get_sweeper_desired_running("graph_auto_update")
            # get_sweeper_desired_running may be sync (SQLiteStorage) or
            # async (PostgresStorage/StorageAdapter).
            if asyncio.iscoroutine(desired):
                desired = await desired
            if desired is False:
                self._running = False
                break

    async def sweep_once(self) -> list[dict[str, Any]]:
        """
        Run a single sweep. Returns the list of newly-escalated
        `graph_outdated` payloads (mainly for tests) -- payloads for
        graphs already known-stale from a prior sweep are not
        repeated, even though they remain unresolved in the outbox.
        """
        list_fn = self._storage.list_graphs
        result = list_fn()
        graphs = await result if asyncio.iscoroutine(result) else result

        supports_outbox = bool(getattr(self._storage, "supports_outbox", False))

        current_outdated: set[str] = set()
        new_payloads: list[dict[str, Any]] = []

        for graph in graphs:
            name = graph.get("name")
            session_id = graph.get("session_id")
            if not name or not session_id:
                continue

            outdated_components = await self._check_graph(name, session_id)
            if not outdated_components:
                continue

            current_outdated.add(name)

            if name in self._known_stale:
                continue  # already escalated on a prior sweep

            payload = {
                "name": name,
                "session_id": session_id,
                "reason": "version_outdated",
                "outdated_components": outdated_components,
                "auto_apply_requested": self._apply_updates,
            }
            new_payloads.append(payload)

            if supports_outbox:
                enqueue_result = self._storage.enqueue_task(
                    task_type=_GRAPH_OUTDATED_TASK_TYPE,
                    session_id=session_id,
                    payload=json.dumps(payload),
                )
                if asyncio.iscoroutine(enqueue_result):
                    await enqueue_result

        self._known_stale = current_outdated

        return new_payloads

    async def _check_graph(self, name: str, session_id: str) -> list[dict[str, Any]]:
        """
        Load `session_id`'s session and return a list of {component,
        graph_version, actual_version} dicts for every Component
        object in it that is behind its GitHub repository's real
        version. Returns [] (never raises) if the session isn't
        available, has no Component objects, or every component
        couldn't be checked (unknown repo, GitHub unreachable, etc)
        -- those are logged, not escalated, since "couldn't verify" is
        not the same claim as "confirmed outdated".
        """
        load_fn = self._storage.load_session
        result = load_fn(session_id)
        session = await result if asyncio.iscoroutine(result) else result
        if session is None:
            logger.warning(
                "GraphAutoUpdateSweeper: session '%s' for graph '%s' is not "
                "available; skipping.",
                session_id,
                name,
            )
            return []

        knowledge_structure = getattr(session, "knowledge_structure", None)
        objects = getattr(knowledge_structure, "objects", None) or []

        components = [
            obj
            for obj in objects
            if getattr(getattr(obj, "identity", None), "type", None) == _COMPONENT_TYPE
            and "version" in getattr(obj, "structure", {})
        ]
        if not components:
            return []

        outdated: list[dict[str, Any]] = []
        for obj in components:
            component_name = obj.identity.name or obj.identity.id
            graph_version = obj.structure.get("version")

            repo, candidate_paths, source = _resolve_component(component_name, obj.structure)
            if repo is None:
                logger.info(
                    "GraphAutoUpdateSweeper: could not determine a GitHub "
                    "repository for component '%s' in graph '%s'; skipping.",
                    component_name,
                    name,
                )
                continue

            fetch_fn = (
                _fetch_package_json_version_sync
                if source == "package_json"
                else _fetch_version_sync
            )
            actual_version, error = await asyncio.to_thread(
                fetch_fn, repo, candidate_paths
            )
            if actual_version is None:
                logger.warning(
                    "GraphAutoUpdateSweeper: could not fetch version for "
                    "component '%s' (repo '%s') in graph '%s': %s",
                    component_name,
                    repo,
                    name,
                    error,
                )
                continue

            if _is_outdated(graph_version, actual_version):
                outdated.append(
                    {
                        "component": component_name,
                        "graph_version": graph_version,
                        "actual_version": actual_version,
                    }
                )

        return outdated

    # ------------------------------------------------------------------
    # Convenience: run a single sweep synchronously (useful in tests)
    # ------------------------------------------------------------------

    async def run_once(self) -> list[dict[str, Any]]:
        """Trigger one sweep immediately, without starting the background loop.

        Unlike the ``_run()`` loop, a raised exception propagates to the
        caller rather than being swallowed -- ``run_once`` is used by
        tests and manual triggers that want to see the failure, not a
        long-running background worker that should keep going. Status
        (``last_run_at``/``last_error``/etc, see ``status()``) is
        recorded either way.
        """
        started_at = datetime.now(UTC)
        try:
            result = await self.sweep_once()
        except Exception as exc:
            self._record_sweep_error(started_at, exc)
            raise
        self._record_sweep_success(started_at, result)
        return result

    # ------------------------------------------------------------------
    # Status (agent_status / list_agents, see cks-mcp)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self.sweeper_status(
            agent_id="graph_auto_update",
            running=self._running,
            interval_seconds=self._interval_seconds,
        )