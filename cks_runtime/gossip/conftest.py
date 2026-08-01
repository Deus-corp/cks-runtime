"""
Test isolation for the ``gossip`` package's persisted state.

``secret.load_secret`` and ``seq_no.SeqNoCounter`` both default to
writing under ``CKS_RUNTIME_DATA_DIR`` (``~/.cks_runtime`` if unset)
when a caller doesn't override the path explicitly. Existing gossip
tests already sidestep ``load_secret`` by always passing an explicit
``secret=`` constant, but nothing sidesteps ``SeqNoCounter``'s own
default the same way -- most ``GossipService``/``GossipServer``
construction sites in this package don't pass ``seq_no_counter``,
relying on (and exercising) the class's real default.

Rather than editing every one of those call sites to pass an explicit
``persist=False`` counter, this autouse fixture redirects
``CKS_RUNTIME_DATA_DIR`` to a fresh ``tmp_path`` for every test in
this directory, so the default path any of them resolves to is always
a throwaway per-test directory instead of the real
``~/.cks_runtime`` -- the same isolation ``secret.py``'s own docstring
describes the env var as being for ("multiple replicas on one machine
... don't collide"), just applied per test instead of per replica.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cks_runtime.gossip.secret import DATA_DIR_ENV_VAR


@pytest.fixture(autouse=True)
def _isolate_gossip_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))