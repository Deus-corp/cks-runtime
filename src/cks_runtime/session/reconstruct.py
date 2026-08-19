"""
Shared helper for reconstructing a session version's state with a
single reload-and-retry on a state-hash mismatch.

``RuntimeSession.get_version_state`` can raise ``ValueError: ...does
not match its recorded hash...`` when a caller's in-memory
``RuntimeSession`` was read mid-write by a concurrent agent (e.g. a
snapshot compaction not yet visible when the version rows were, or
vice versa). This is usually a transient snapshot-consistency race,
not a genuine corruption: reloading the session fresh from storage
and reconstructing again clears the race without masking a real
problem, since a genuinely bad patch chain still fails the same way
on the retry.

This was originally implemented only inside
``cks_runtime.projection.outbox_worker.OutboxEmbeddingWorker``. It is
factored out here so any other consumer that reconstructs historical
version state (other outbox-driven agents, MCP tool handlers, etc.)
can get the same one-shot reload-and-retry behavior without
duplicating it.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _SupportsLoadSession(Protocol):
    async def load_session(self, session_id: str) -> Any: ...


async def reconstruct_with_retry(
    storage: _SupportsLoadSession,
    session_id: str,
    session: Any,
    version_id: str,
    core_bridge: Any = None,
) -> Any:
    """
    Reconstruct ``version_id``'s Knowledge Structure via
    ``session.get_version_state``, retrying exactly once against a
    freshly-reloaded ``RuntimeSession`` (via ``storage.load_session``)
    if the first attempt fails on a state-hash mismatch.

    Any other ``ValueError`` (missing version, no ``core_bridge`` for
    a delta version, inconsistent history, etc.) is not
    reload-and-retried -- reloading the same session can't fix those
    -- and propagates immediately. A mismatch that persists after the
    reload is a genuine corruption, not a race: it also propagates,
    so the caller's own retry/dead-letter accounting (if any) applies
    to it exactly as it would to any other failure.
    """
    try:
        return session.get_version_state(version_id, core_bridge)
    except ValueError as exc:
        if "does not match its recorded hash" not in str(exc):
            raise
        logger.warning(
            "Hash mismatch reconstructing version %s for session %s; "
            "reloading session from storage and retrying once: %s",
            version_id, session_id, exc,
        )
        fresh_session = await storage.load_session(session_id)
        if fresh_session is None:
            raise
        return fresh_session.get_version_state(version_id, core_bridge)