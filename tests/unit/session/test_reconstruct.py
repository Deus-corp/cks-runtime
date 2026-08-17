"""
Unit tests for ``cks_runtime.session.reconstruct.reconstruct_with_retry``,
the shared helper factored out of ``OutboxEmbeddingWorker`` so any
consumer that reconstructs historical version state gets the same
one-shot reload-and-retry behavior on a state-hash mismatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cks_runtime.session.reconstruct import reconstruct_with_retry

pytestmark = pytest.mark.asyncio


def _mismatch_error() -> ValueError:
    return ValueError(
        "Reconstructed state for version 'v2' does not match its "
        "recorded hash (expected 'abc', got 'def')."
    )


async def test_returns_state_on_first_success():
    session = MagicMock()
    session.get_version_state.return_value = "structure-v2"
    storage = MagicMock()
    storage.load_session = AsyncMock()

    result = await reconstruct_with_retry(storage, "s1", session, "v2", core_bridge=None)

    assert result == "structure-v2"
    storage.load_session.assert_not_awaited()


async def test_retries_once_after_reload_on_hash_mismatch():
    session = MagicMock()
    session.get_version_state.side_effect = _mismatch_error()

    fresh_session = MagicMock()
    fresh_session.get_version_state.return_value = "structure-v2-fresh"

    storage = MagicMock()
    storage.load_session = AsyncMock(return_value=fresh_session)

    result = await reconstruct_with_retry(storage, "s1", session, "v2", core_bridge="bridge")

    assert result == "structure-v2-fresh"
    storage.load_session.assert_awaited_once_with("s1")
    fresh_session.get_version_state.assert_called_once_with("v2", "bridge")


async def test_persistent_mismatch_after_reload_propagates():
    session = MagicMock()
    session.get_version_state.side_effect = _mismatch_error()

    fresh_session = MagicMock()
    fresh_session.get_version_state.side_effect = _mismatch_error()

    storage = MagicMock()
    storage.load_session = AsyncMock(return_value=fresh_session)

    with pytest.raises(ValueError, match="does not match its recorded hash"):
        await reconstruct_with_retry(storage, "s1", session, "v2", core_bridge=None)

    storage.load_session.assert_awaited_once_with("s1")


async def test_non_hash_mismatch_value_error_propagates_without_reload():
    session = MagicMock()
    session.get_version_state.side_effect = ValueError("version 'v2' not found")

    storage = MagicMock()
    storage.load_session = AsyncMock()

    with pytest.raises(ValueError, match="not found"):
        await reconstruct_with_retry(storage, "s1", session, "v2", core_bridge=None)

    storage.load_session.assert_not_awaited()


async def test_reload_returning_none_propagates_original_error():
    session = MagicMock()
    session.get_version_state.side_effect = _mismatch_error()

    storage = MagicMock()
    storage.load_session = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="does not match its recorded hash"):
        await reconstruct_with_retry(storage, "s1", session, "v2", core_bridge=None)