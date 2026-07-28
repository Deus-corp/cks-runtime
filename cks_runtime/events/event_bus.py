"""
Runtime Event Bus.

The Event Bus is responsible for delivering RuntimeEvents
to interested subscribers.

It owns event routing.

It never owns Runtime behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import TypeVar, cast

from .runtime_event import RuntimeEvent

EventHandler = Callable[[RuntimeEvent], None]

_E = TypeVar("_E", bound=RuntimeEvent)


class EventBus:
    """
    Canonical Runtime Event Bus.

    Responsibilities:

        • publish events

        • subscribe handlers

        • unsubscribe handlers

        • maintain event history

    The Event Bus performs synchronous delivery.

    Future versions may introduce asynchronous transports.
    """

    def __init__(self) -> None:

        self._subscribers: defaultdict[
            type[RuntimeEvent],
            list[EventHandler],
        ] = defaultdict(list)

        self._history: list[
            RuntimeEvent
        ] = []

    #
    # Subscription API
    #

    def subscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None],
    ) -> None:
        """
        Subscribe a handler to an event type.

        ``handler`` may be typed for ``event_type`` specifically (e.g.
        ``Callable[[VersionCreated], None]``) rather than the base
        ``EventHandler``: publish() only ever invokes it with an
        instance of the type it was registered under (exact-type
        dispatch), so the narrower parameter type is sound even though
        it's stored internally as the erased ``EventHandler`` alias.
        """
        stored = cast(EventHandler, handler)

        if stored not in self._subscribers[event_type]:

            self._subscribers[event_type].append(
                stored,
            )

    def unsubscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None],
    ) -> None:
        """
        Remove an event handler.
        """
        stored = cast(EventHandler, handler)

        if stored in self._subscribers[event_type]:

            self._subscribers[event_type].remove(
                stored,
            )

    #
    # Publishing
    #

    def publish(
        self,
        event: RuntimeEvent,
    ) -> None:
        """
        Publish a RuntimeEvent.

        Delivery order:

            store history

                ↓

            deliver to exact type subscribers

                ↓

            deliver to RuntimeEvent subscribers
        """

        self._history.append(
            event,
        )

        #
        # Exact type
        #

        for handler in self._subscribers[
            type(event)
        ]:

            handler(event)

        #
        # Global RuntimeEvent subscribers
        #

        if not isinstance(
            event,
            RuntimeEvent,
        ):
            return

        for handler in self._subscribers[
            RuntimeEvent
        ]:

            handler(event)

    #
    # History
    #

    def history(
        self,
    ) -> tuple[
        RuntimeEvent,
        ...
    ]:
        """
        Immutable event history.
        """

        return tuple(
            self._history
        )

    def clear(
        self,
    ) -> None:
        """
        Remove all stored events.
        """

        self._history.clear()

    #
    # Introspection
    #

    def subscribers(
        self,
        event_type: type[RuntimeEvent],
    ) -> tuple[
        EventHandler,
        ...
    ]:
        """
        Return subscribers of an event type.
        """

        return tuple(
            self._subscribers[event_type]
        )

    def __len__(
        self,
    ) -> int:

        return len(
            self._history
        )

    def __iter__(
        self,
    ) -> Iterator[
        RuntimeEvent
    ]:

        return iter(
            self._history
        )