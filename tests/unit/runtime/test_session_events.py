"""
Regression tests for ``SessionCreated``/``SessionClosed`` actually being
published on the Runtime's EventBus.

Both event types have existed in ``events/runtime_event.py`` since
before this test file, and cks-mcp's ``observability.py`` (plus its own
CHANGELOG/architecture docs) already documents subscribing to them for
its structured lifecycle log -- but nothing in ``Runtime`` ever called
``events.publish(SessionCreated(...))`` / ``SessionClosed(...)``, so
that logging, and anything else built on the same subscription (e.g.
cks-mcp's optional gossip integration tracking sessions automatically),
silently never fired. These tests pin the fix: ``create_session``,
``create_branch``, and ``close_session`` must each publish their
corresponding event, carrying the right ``session_id``.
"""

from __future__ import annotations

import pytest

from cks_runtime.events.runtime_event import SessionClosed, SessionCreated
from cks_runtime.runtime import Runtime

pytestmark = pytest.mark.asyncio


async def test_create_session_publishes_session_created():
    runtime = Runtime()
    seen: list[SessionCreated] = []
    runtime.events.subscribe(SessionCreated, seen.append)

    session = await runtime.create_session({})

    assert len(seen) == 1
    assert seen[0].session_id == session.session_id


async def test_create_branch_publishes_session_created_for_the_branch():
    runtime = Runtime()
    seen: list[SessionCreated] = []
    runtime.events.subscribe(SessionCreated, seen.append)

    session = await runtime.create_session({})
    seen.clear()  # only interested in the branch's own event here

    branch = await runtime.create_branch(session)

    assert len(seen) == 1
    assert seen[0].session_id == branch.session_id
    assert branch.session_id != session.session_id


async def test_close_session_publishes_session_closed():
    runtime = Runtime()
    seen: list[SessionClosed] = []
    runtime.events.subscribe(SessionClosed, seen.append)

    session = await runtime.create_session({})
    await runtime.close_session(session.session_id)

    assert len(seen) == 1
    assert seen[0].session_id == session.session_id


async def test_close_session_is_a_noop_for_unknown_session_id():
    """No SessionClosed for an id that was never a real session --
    matches close_session's own existing no-op-on-unknown-id behaviour
    (it never raises), rather than fabricating an event for a session
    that never existed."""
    runtime = Runtime()
    seen: list[SessionClosed] = []
    runtime.events.subscribe(SessionClosed, seen.append)

    await runtime.close_session("does-not-exist")

    assert seen == []
