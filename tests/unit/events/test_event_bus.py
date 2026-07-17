"""Unit tests for EventBus."""

from __future__ import annotations

import pytest

from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import (
    SessionCreated,
    SessionClosed,
)


@pytest.fixture
def bus():
    return EventBus()


def test_publish_single_subscriber(bus):
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe(SessionCreated, handler)

    event = SessionCreated(session_id="s1")

    bus.publish(event)

    assert received == [event]


def test_publish_multiple_subscribers(bus):
    first = []
    second = []

    bus.subscribe(
        SessionCreated,
        lambda event: first.append(event),
    )

    bus.subscribe(
        SessionCreated,
        lambda event: second.append(event),
    )

    event = SessionCreated(session_id="s1")

    bus.publish(event)

    assert first == [event]
    assert second == [event]


def test_unsubscribe(bus):
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe(
        SessionCreated,
        handler,
    )

    bus.unsubscribe(
        SessionCreated,
        handler,
    )

    bus.publish(
        SessionCreated(session_id="s1"),
    )

    assert received == []


def test_clear_history(bus):
    """clear() removes all stored events but keeps subscribers."""
    received = []
    bus.subscribe(SessionCreated, lambda e: received.append(e))
    bus.publish(SessionCreated(session_id="s1"))
    assert len(bus.history()) == 1
    bus.clear()
    assert len(bus.history()) == 0
    # подписчик остался – событие должно быть доставлено
    bus.publish(SessionCreated(session_id="s2"))
    assert len(received) == 2  # первое событие тоже было получено до clear


def test_event_type_isolated(bus):
    created = []
    closed = []

    bus.subscribe(
        SessionCreated,
        lambda event: created.append(event),
    )

    bus.subscribe(
        SessionClosed,
        lambda event: closed.append(event),
    )

    bus.publish(
        SessionCreated(session_id="created"),
    )

    assert len(created) == 1
    assert len(closed) == 0


def test_publish_without_subscribers(bus):
    """
    Publishing an event without subscribers
    should never fail.
    """

    bus.publish(
        SessionCreated(session_id="s1"),
    )


def test_duplicate_subscription_is_ignored(bus):
    """
    Registering the same callback twice should
    not result in duplicate delivery.
    """

    received = []

    def handler(event):
        received.append(event)

    bus.subscribe(
        SessionCreated,
        handler,
    )

    bus.subscribe(
        SessionCreated,
        handler,
    )

    bus.publish(
        SessionCreated(session_id="s1"),
    )

    assert len(received) == 1


def test_unsubscribe_missing_handler_is_noop(bus):
    """
    Removing a non-existent subscription should
    not raise.
    """

    def handler(event):
        pass

    bus.unsubscribe(
        SessionCreated,
        handler,
    )


def test_publish_preserves_order(bus):
    """
    Subscribers should be called
    in registration order.
    """

    calls = []

    bus.subscribe(
        SessionCreated,
        lambda event: calls.append(1),
    )

    bus.subscribe(
        SessionCreated,
        lambda event: calls.append(2),
    )

    bus.subscribe(
        SessionCreated,
        lambda event: calls.append(3),
    )

    bus.publish(
        SessionCreated(session_id="s1"),
    )

    assert calls == [1, 2, 3]