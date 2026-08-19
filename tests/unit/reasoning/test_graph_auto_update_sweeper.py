"""
Unit tests for GraphAutoUpdateSweeper.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import cks
import pytest

from cks_runtime.reasoning.graph_auto_update_sweeper import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    GraphAutoUpdateSweeper,
    _repo_from_url,
)
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage

# pyproject.toml sets asyncio_mode = "auto" for this project's test suite
# (see tests/unit/reasoning/test_graph_freshness_sweeper.py and friends,
# which use @pytest.mark.asyncio the same way); mirroring that here.

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def _component_ks(
    name: str,
    version: str,
    *,
    repo_url: str | None = None,
    version_source: str | None = None,
) -> object:
    structure: dict[str, object] = {"version": version}
    if repo_url is not None:
        structure["repo_url"] = repo_url
    if version_source is not None:
        structure["version_source"] = version_source
    obj = {
        "identity": {"id": f"comp-{name}", "type": "Component", "name": name},
        "structure": structure,
    }
    return cks.parse(json.dumps({"objects": [obj]}))


def _fake_package_json_response(version: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text=json.dumps({"version": version}))


def _plain_ks() -> object:
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_session(session_id: str, knowledge_structure: object) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=knowledge_structure, session_id=session_id)
    s.closed = False
    return s


def _register(storage: SQLiteStorage, name: str, session_id: str, ks: object) -> None:
    storage.save_session(_make_session(session_id, ks))
    storage.register_graph(name, session_id)


def _fake_version_response(version: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text=f'__version__ = "{version}"')


def _outbox_payloads(storage: SQLiteStorage, task_type: str = "graph_outdated") -> list[dict]:
    rows = storage._conn.execute(
        "SELECT payload FROM cks_outbox_tasks WHERE task_type = ?", (task_type,)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# sweep_once: core detection behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_outdated_component_and_escalates(storage):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    payload = escalated[0]
    assert payload["name"] == "g1"
    assert payload["session_id"] == "s1"
    assert payload["reason"] == "version_outdated"
    assert payload["outdated_components"] == [
        {"component": "cks-core", "graph_version": "1.0.0", "actual_version": "2.0.0"}
    ]

    outbox = _outbox_payloads(storage)
    assert len(outbox) == 1
    assert outbox[0]["name"] == "g1"


@pytest.mark.asyncio
async def test_does_not_escalate_up_to_date_component(storage):
    ks = _component_ks("cks-core", "2.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert escalated == []
    assert _outbox_payloads(storage) == []


@pytest.mark.asyncio
async def test_ahead_version_not_escalated(storage):
    ks = _component_ks("cks-core", "3.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_no_component_objects_no_escalation(storage):
    _register(storage, "g1", "s1", _plain_ks())

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get"
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert escalated == []
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_repo_url_component_resolved_and_checked(storage):
    ks = _component_ks(
        "widget-lib", "1.0.0", repo_url="https://github.com/acme/widget-lib"
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("1.1.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["outdated_components"][0]["component"] == "widget-lib"


@pytest.mark.asyncio
async def test_unknown_repo_component_skipped_not_escalated(storage):
    ks = _component_ks("mystery-thing", "1.0.0")  # no repo_url, not in known map
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get"
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert escalated == []
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# version_source: "package.json" (JS/TS components)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_package_json_component_resolved_and_checked(storage):
    ks = _component_ks(
        "cks-studio",
        "v0.18.0",
        repo_url="https://github.com/punctumactus/cks-studio",
        version_source="package.json",
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_package_json_response("0.19.0"),
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["outdated_components"][0] == {
        "component": "cks-studio",
        "graph_version": "v0.18.0",
        "actual_version": "0.19.0",
    }
    # First candidate path is the repo-root package.json.
    called_url = mock_get.call_args_list[0].args[0]
    assert called_url == "https://raw.githubusercontent.com/punctumactus/cks-studio/main/package.json"


@pytest.mark.asyncio
async def test_package_json_up_to_date_not_escalated(storage):
    ks = _component_ks(
        "cks-studio",
        "0.19.0",
        repo_url="https://github.com/punctumactus/cks-studio",
        version_source="package.json",
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_package_json_response("0.19.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert escalated == []


@pytest.mark.asyncio
async def test_component_without_version_source_falls_back_to_python(storage):
    # No version_source set -- must still use the _version.py convention,
    # not package.json, even though the fake response below would parse
    # as neither if the wrong regex/parser were used.
    ks = _component_ks(
        "widget-lib", "1.0.0", repo_url="https://github.com/acme/widget-lib"
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("1.1.0"),
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    called_url = mock_get.call_args_list[0].args[0]
    assert called_url.endswith("/_version.py")


@pytest.mark.asyncio
async def test_known_component_with_version_source_still_uses_package_json(storage):
    # cks-studio-like case: a name in _KNOWN_COMPONENTS-style ecosystem
    # map would normally resolve to a Python _version.py path, but an
    # explicit version_source override on the Component must still win.
    ks = _component_ks(
        "cks-core",
        "1.0.0",
        repo_url="https://github.com/punctumactus/cks-core",
        version_source="package.json",
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_package_json_response("2.0.0"),
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    called_url = mock_get.call_args_list[0].args[0]
    assert called_url.endswith("package.json")


@pytest.mark.asyncio
async def test_package_json_missing_repo_file_not_escalated(storage):
    ks = _component_ks(
        "ghost-app",
        "1.0.0",
        repo_url="https://github.com/acme/ghost-app",
        version_source="package.json",
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=SimpleNamespace(status_code=404, text="Not Found"),
    ) as mock_get:
        escalated = await sweeper.sweep_once()

    assert escalated == []
    # Tried both candidate package.json locations before giving up.
    assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_package_json_invalid_json_not_escalated(storage):
    ks = _component_ks(
        "broken-app",
        "1.0.0",
        repo_url="https://github.com/acme/broken-app",
        version_source="package.json",
    )
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=SimpleNamespace(status_code=200, text="{not valid json"),
    ):
        escalated = await sweeper.sweep_once()

    assert escalated == []


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_does_not_reescalate_same_graph(storage):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        first = await sweeper.sweep_once()
        second = await sweeper.sweep_once()

    assert len(first) == 1
    assert second == []
    # Only one outbox task written across both sweeps.
    assert len(_outbox_payloads(storage)) == 1


@pytest.mark.asyncio
async def test_reescalates_after_becoming_fresh_then_stale_again(storage):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        await sweeper.sweep_once()

    # Graph becomes fresh (re-registered with matching version).
    ks_fresh = _component_ks("cks-core", "2.0.0")
    _register(storage, "g1", "s1", ks_fresh)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        fresh_sweep = await sweeper.sweep_once()
    assert fresh_sweep == []

    # Regresses again -- should be escalated once more, not suppressed.
    ks_stale_again = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks_stale_again)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        third = await sweeper.sweep_once()
    assert len(third) == 1


# ---------------------------------------------------------------------------
# InMemoryStorage: no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_op_on_in_memory_storage():
    storage = InMemoryStorage()
    sweeper = GraphAutoUpdateSweeper(storage)

    # supports_outbox is False for InMemoryStorage, so start() should
    # not spin up a background task at all.
    await sweeper.start()
    assert sweeper._task is None
    await sweeper.stop()

    # sweep_once still runs safely (list_graphs is a no-op returning
    # []), it just never has anything to escalate.
    escalated = await sweeper.sweep_once()
    assert escalated == []


# ---------------------------------------------------------------------------
# auto_apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_updates_flag_recorded_but_still_escalates(storage):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage, apply_updates=True)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        return_value=_fake_version_response("2.0.0"),
    ):
        escalated = await sweeper.sweep_once()

    assert len(escalated) == 1
    assert escalated[0]["auto_apply_requested"] is True
    outbox = _outbox_payloads(storage)
    assert outbox[0]["auto_apply_requested"] is True


# ---------------------------------------------------------------------------
# GitHub unreachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_unreachable_does_not_crash_and_logs(storage, caplog):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage)
    with patch(
        "cks_runtime.reasoning.graph_auto_update_sweeper.safe_get",
        side_effect=ConnectionError("boom"),
    ), caplog.at_level("WARNING"):
        escalated = await sweeper.sweep_once()

    assert escalated == []
    assert any("could not fetch version" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_sweep_survives_general_exception_in_run_loop(storage):
    ks = _component_ks("cks-core", "1.0.0")
    _register(storage, "g1", "s1", ks)

    sweeper = GraphAutoUpdateSweeper(storage, interval_seconds=1000)
    with patch.object(
        sweeper, "sweep_once", side_effect=RuntimeError("boom")
    ):
        await sweeper.start()
        # Give the background task a tick to run and hit the exception.
        import asyncio

        await asyncio.sleep(0.05)
        assert sweeper._running is True  # loop kept running despite the error
        await sweeper.stop()


# ---------------------------------------------------------------------------
# start/stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_lifecycle(storage):
    sweeper = GraphAutoUpdateSweeper(storage, interval_seconds=1000)
    assert sweeper._task is None

    await sweeper.start()
    assert sweeper._task is not None
    assert sweeper._running is True

    # start() again is a no-op while already running.
    task_before = sweeper._task
    await sweeper.start()
    assert sweeper._task is task_before

    await sweeper.stop()
    assert sweeper._task is None
    assert sweeper._running is False


def test_default_interval_matches_module_constant():
    assert DEFAULT_SWEEP_INTERVAL_SECONDS == 3600


# ---------------------------------------------------------------------------
# _repo_from_url: full URL and bare "owner/repo" forms (GitHub username
# migration Deus-corp -> punctumactus; repo strings may come from stored
# graph data using either form).
# ---------------------------------------------------------------------------


def test_repo_from_url_full_https_url():
    assert _repo_from_url("https://github.com/punctumactus/cks-runtime") == "punctumactus/cks-runtime"


def test_repo_from_url_full_https_url_with_git_suffix():
    assert _repo_from_url("https://github.com/punctumactus/cks-runtime.git") == "punctumactus/cks-runtime"


def test_repo_from_url_bare_owner_repo():
    assert _repo_from_url("punctumactus/cks-runtime") == "punctumactus/cks-runtime"


def test_repo_from_url_bare_owner_repo_with_git_suffix():
    assert _repo_from_url("punctumactus/cks-runtime.git") == "punctumactus/cks-runtime"


def test_repo_from_url_non_github_url_rejected():
    assert _repo_from_url("https://gitlab.com/punctumactus/cks-runtime") is None


def test_repo_from_url_garbage_rejected():
    assert _repo_from_url("not-a-repo") is None
    assert _repo_from_url("") is None