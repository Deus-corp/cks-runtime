"""
Runtime Event Bus.

The Event Bus is responsible for delivering RuntimeEvents
to interested subscribers.

It owns event routing.

It never owns Runtime behaviour.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from inspect import isawaitable
from typing import TypeVar, cast

from .runtime_event import RuntimeEvent

EventHandler = Callable[[RuntimeEvent], "None | Awaitable[None]"]

_E = TypeVar("_E", bound=RuntimeEvent)


class EventBus:
    """
    Canonical Runtime Event Bus.

    Responsibilities:

        • publish events

        • subscribe handlers

        • unsubscribe handlers

        • maintain event history

    The Event Bus delivers events sequentially, one handler at a time,
    in subscription order. A handler may be synchronous or an
    ``async def`` -- ``publish()`` awaits it either way.
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
        handler: Callable[[_E], None | Awaitable[None]],
    ) -> None:
        """
        Subscribe a handler to an event type.

        ``handler`` may be typed for ``event_type`` specifically (e.g.
        ``Callable[[VersionCreated], None]``) rather than the base
        ``EventHandler``: publish() only ever invokes it with an
        instance of the type it was registered under (exact-type
        dispatch), so the narrower parameter type is sound even though
        it's stored internally as the erased ``EventHandler`` alias.

        ``handler`` may be a plain function or an ``async def`` --
        ``publish()`` awaits the call only when it actually returns an
        awaitable, so existing synchronous subscribers (e.g. the
        logging handlers ``cks-mcp`` registers) keep working
        unchanged.
        """
        stored = cast(EventHandler, handler)

        if stored not in self._subscribers[event_type]:

            self._subscribers[event_type].append(
                stored,
            )

    def unsubscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None | Awaitable[None]],
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

    async def publish(
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

        Delivery is still sequential (one handler at a time, in
        subscription order) -- only whether each individual handler
        call is awaited depends on whether that handler is a coroutine
        function. This is not fan-out/concurrent delivery; it's the
        same ordering guarantee as before, with a hook for handlers
        that need to await something (e.g. ``EmbeddingProjection``
        writing to the outbox table).
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

            result = handler(event)
            if isawaitable(result):
                await result

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

            result = handler(event)
            if isawaitable(result):
                await result

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