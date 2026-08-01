"""Unit tests for GossipFilter (ADR-008)."""

from __future__ import annotations

import time

from cks_runtime.gossip.filter import GossipFilter


class TestBasicAcceptance:
    def test_accepts_a_well_formed_first_message(self):
        f = GossipFilter()
        assert f.check("replica-a", "nonce-1", 1, _now_ms()) is True

    def test_rejects_missing_sender(self):
        f = GossipFilter()
        assert f.check("", "nonce-1", 1, _now_ms()) is False
        assert f.check("   ", "nonce-1", 1, _now_ms()) is False

    def test_none_fields_are_skipped_not_rejected(self):
        # nonce/seq_no/timestamp_ms are each optional -- None simply
        # skips that particular check rather than failing it.
        f = GossipFilter()
        assert f.check("replica-a", None, None, None) is True


class TestTimestampAndClockSkew:
    def test_rejects_timestamp_outside_skew_window(self):
        f = GossipFilter(max_clock_skew_ms=1_000)
        stale = _now_ms() - 5_000
        assert f.check("replica-a", "n1", 1, stale) is False

    def test_accepts_timestamp_within_skew_window(self):
        f = GossipFilter(max_clock_skew_ms=10_000)
        recent = _now_ms() - 500
        assert f.check("replica-a", "n1", 1, recent) is True

    def test_rejects_invalid_timestamp_type(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 1, "not-a-number") is False  # type: ignore[arg-type]

    def test_ttl_expiry_rejects_old_message_even_within_skew(self):
        f = GossipFilter(max_clock_skew_ms=60_000)
        ts = _now_ms() - 5_000
        assert f.check("replica-a", "n1", 1, ts, ttl_ms=1_000) is False

    def test_ttl_negative_is_rejected(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 1, _now_ms(), ttl_ms=-1) is False


class TestNonceReplayProtection:
    def test_rejects_replayed_nonce_from_same_sender(self):
        f = GossipFilter()
        assert f.check("replica-a", "dup", 1, _now_ms()) is True
        assert f.check("replica-a", "dup", 2, _now_ms()) is False

    def test_same_nonce_from_different_senders_is_independent(self):
        f = GossipFilter()
        assert f.check("replica-a", "shared-nonce", 1, _now_ms()) is True
        assert f.check("replica-b", "shared-nonce", 1, _now_ms()) is True

    def test_rejects_empty_nonce(self):
        f = GossipFilter()
        assert f.check("replica-a", "", 1, _now_ms()) is False

    def test_nonce_cache_evicts_oldest_beyond_max(self):
        f = GossipFilter(max_nonce_cache=2)
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-a", "n2", 2, _now_ms()) is True
        assert f.check("replica-a", "n3", 3, _now_ms()) is True
        # n1 was evicted, so it's accepted again as if new.
        assert f.check("replica-a", "n1", 4, _now_ms()) is True


class TestSequenceProtection:
    def test_rejects_non_monotonic_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 5, _now_ms()) is True
        assert f.check("replica-a", "n2", 5, _now_ms()) is False
        assert f.check("replica-a", "n3", 4, _now_ms()) is False

    def test_accepts_increasing_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-a", "n2", 2, _now_ms()) is True
        assert f.check("replica-a", "n3", 10, _now_ms()) is True

    def test_rejects_negative_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", -1, _now_ms()) is False


class TestResetAndClear:
    def test_reset_clears_one_sender_only(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-b", "n1", 1, _now_ms()) is True

        f.reset("replica-a")

        # replica-a's nonce/seq state is gone -- same nonce/seq accepted again.
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        # replica-b untouched -- still rejects the replay.
        assert f.check("replica-b", "n1", 1, _now_ms()) is False

    def test_clear_resets_every_sender(self):
        f = GossipFilter()
        f.check("replica-a", "n1", 1, _now_ms())
        f.check("replica-b", "n1", 1, _now_ms())

        f.clear()

        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-b", "n1", 1, _now_ms()) is True


class TestStats:
    def test_stats_reflect_cache_state(self):
        f = GossipFilter(max_clock_skew_ms=5_000, max_nonce_cache=100)
        f.check("replica-a", "n1", 1, _now_ms())
        f.check("replica-b", "n1", 1, _now_ms())

        stats = f.stats()
        assert stats["senders_with_nonces"] == 2
        assert stats["senders_with_sequences"] == 2
        assert stats["nonce_count"] == 2
        assert stats["max_nonce_cache"] == 100
        assert stats["max_clock_skew_ms"] == 5_000


def _now_ms() -> int:
    return int(time.time() * 1000)