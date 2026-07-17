"""
Runtime Event Bus.

The Event Bus is responsible for delivering RuntimeEvents
to interested subscribers.

It owns event routing.

It never owns Runtime behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from typing import DefaultDict
from typing import Type

from .runtime_event import RuntimeEvent


EventHandler = Callable[[RuntimeEvent], None]


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

        self._subscribers: DefaultDict[
            Type[RuntimeEvent],
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
        event_type: Type[RuntimeEvent],
        handler: EventHandler,
    ) -> None:
        """
        Subscribe a handler to an event type.
        """

        if handler not in self._subscribers[event_type]:

            self._subscribers[event_type].append(
                handler,
            )

    def unsubscribe(
        self,
        event_type: Type[RuntimeEvent],
        handler: EventHandler,
    ) -> None:
        """
        Remove an event handler.
        """

        if handler in self._subscribers[event_type]:

            self._subscribers[event_type].remove(
                handler,
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
        event_type: Type[RuntimeEvent],
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