"""
``PeerScheduler`` -- decides which peer to gossip with next, and when
a peer is temporarily skipped after failing (ADR-008: "Nothing decides
when to gossip, to whom, or how to escalate a conflict found outside a
synchronous caller").

This is an original implementation for this repo, not a port. It's
informed by the general shape of gossip peer scheduling -- track a
rolling success/failure count per peer, weight peer selection toward
peers that have been responding, and back off exponentially from
peers that are currently failing so one unreachable peer doesn't
dominate every gossip round -- but the earlier session that reviewed a
different project's gossip implementation for reusable pieces did not
have that project's scheduler source available to adapt directly (only
its transport lifecycle and filter modules were), so this was designed
fresh against this repo's own needs instead of copied.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass


@dataclass(slots=True)
class PeerStats:
    """Rolling health record for one gossip peer."""

    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_attempt_ms: int | None = None
    #: Epoch-ms before which this peer is skipped by ``eligible_peers``
    #: / ``choose_peer``. ``0`` means "always eligible" (never failed,
    #: or backoff already elapsed).
    backoff_until_ms: int = 0

    @property
    def weight(self) -> float:
        """
        Selection weight for ``choose_peer``.

        Laplace-smoothed success rate (``(successes + 1) / (successes
        + failures + 2)``) rather than a raw ratio, so a peer with no
        history yet gets a mid-range weight (0.5) instead of either
        being starved (0, if unseen defaulted to worst) or
        overwhelming actually-reliable peers (1.0, if unseen defaulted
        to best) before any evidence exists either way.
        """
        return (self.successes + 1) / (self.successes + self.failures + 2)


class PeerScheduler:
    """
    Tracks per-peer health for a fixed set of gossip peers and chooses
    which one to gossip with next.

    Not thread-safe (no ``GossipService`` in this repo is expected to
    run more than one gossip round concurrently against the same
    scheduler); an ``asyncio``-single-threaded event loop is the
    assumed caller, matching every other Runtime component.
    """

    def __init__(
        self,
        peers: list[str],
        *,
        base_backoff_s: float = 1.0,
        max_backoff_s: float = 300.0,
    ) -> None:
        if base_backoff_s <= 0:
            raise ValueError(f"base_backoff_s must be > 0, got {base_backoff_s!r}.")
        if max_backoff_s < base_backoff_s:
            raise ValueError(
                f"max_backoff_s ({max_backoff_s!r}) must be >= "
                f"base_backoff_s ({base_backoff_s!r})."
            )

        self._stats: dict[str, PeerStats] = {peer: PeerStats() for peer in peers}
        self.base_backoff_s = base_backoff_s
        self.max_backoff_s = max_backoff_s

    @property
    def peers(self) -> tuple[str, ...]:
        return tuple(self._stats.keys())

    def add_peer(self, peer: str) -> None:
        """Start tracking a new peer, if not already known."""
        self._stats.setdefault(peer, PeerStats())

    def remove_peer(self, peer: str) -> None:
        """Stop tracking a peer entirely (e.g. removed from config)."""
        self._stats.pop(peer, None)

    def stats_for(self, peer: str) -> PeerStats:
        return self._stats.setdefault(peer, PeerStats())

    # ------------------------------------------------------------------
    # Outcome recording
    # ------------------------------------------------------------------

    def record_success(self, peer: str, *, now_ms: int | None = None) -> None:
        """Record a successful exchange -- clears any active backoff."""
        stats = self.stats_for(peer)
        stats.successes += 1
        stats.consecutive_failures = 0
        stats.backoff_until_ms = 0
        stats.last_attempt_ms = now_ms if now_ms is not None else _now_ms()

    def record_failure(self, peer: str, *, now_ms: int | None = None) -> None:
        """
        Record a failed exchange (``GossipTransportError``) and apply
        exponential backoff: ``base_backoff_s * 2 ** (consecutive_failures
        - 1)``, capped at ``max_backoff_s``.
        """
        resolved_now = now_ms if now_ms is not None else _now_ms()
        stats = self.stats_for(peer)
        stats.failures += 1
        stats.consecutive_failures += 1
        stats.last_attempt_ms = resolved_now

        backoff_s = min(
            self.base_backoff_s * (2 ** (stats.consecutive_failures - 1)),
            self.max_backoff_s,
        )
        stats.backoff_until_ms = resolved_now + int(backoff_s * 1000)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def eligible_peers(self, *, now_ms: int | None = None) -> list[str]:
        """Peers not currently serving out a backoff period."""
        resolved_now = now_ms if now_ms is not None else _now_ms()
        return [
            peer
            for peer, stats in self._stats.items()
            if stats.backoff_until_ms <= resolved_now
        ]

    def choose_peer(
        self,
        *,
        now_ms: int | None = None,
        rng: random.Random | None = None,
    ) -> str | None:
        """
        Pick one eligible peer, weighted toward peers with a better
        recent success rate. Returns ``None`` if every known peer is
        currently backed off (or there are no peers at all).
        """
        candidates = self.eligible_peers(now_ms=now_ms)
        if not candidates:
            return None

        resolved_rng = rng if rng is not None else random
        weights = [self._stats[peer].weight for peer in candidates]
        return resolved_rng.choices(candidates, weights=weights, k=1)[0]


def _now_ms() -> int:
    return int(time.time() * 1000)