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
    def test_rejects_exact_duplicate_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 5, _now_ms()) is True
        assert f.check("replica-a", "n2", 5, _now_ms()) is False

    def test_accepts_increasing_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-a", "n2", 2, _now_ms()) is True
        assert f.check("replica-a", "n3", 10, _now_ms()) is True

    def test_rejects_negative_sequence(self):
        f = GossipFilter()
        assert f.check("replica-a", "n1", -1, _now_ms()) is False

    def test_accepts_slightly_out_of_order_sequence_within_window(self):
        """
        A seq_no arriving a little behind the current high-water mark
        (e.g. a slower of two concurrent requests from senders sharing
        one seq_no stream, see GossipFilter's module docstring) is
        legitimate reordering, not a replay, and must be accepted the
        first time it's seen.
        """
        f = GossipFilter(max_seq_reorder_window=10)
        assert f.check("replica-a", "n1", 5, _now_ms()) is True
        # 4 hasn't been seen before and is within the reorder window
        # behind the high-water mark (5) -- accepted.
        assert f.check("replica-a", "n2", 4, _now_ms()) is True
        # The high-water mark itself does not move backwards.
        assert f.check("replica-a", "n3", 6, _now_ms()) is True

    def test_rejects_replay_of_an_already_accepted_out_of_order_sequence(self):
        f = GossipFilter(max_seq_reorder_window=10)
        assert f.check("replica-a", "n1", 5, _now_ms()) is True
        assert f.check("replica-a", "n2", 4, _now_ms()) is True
        # seq=4 was already accepted once -- replaying it is rejected
        # even though it's still within the reorder window.
        assert f.check("replica-a", "n3", 4, _now_ms()) is False

    def test_rejects_sequence_too_far_behind_the_window(self):
        f = GossipFilter(max_seq_reorder_window=3)
        assert f.check("replica-a", "n1", 100, _now_ms()) is True
        # 96 is more than 3 behind the high-water mark (100) -- too
        # old to plausibly be ordinary reordering, rejected outright.
        assert f.check("replica-a", "n2", 96, _now_ms()) is False

    def test_reorder_window_is_bounded_per_sender(self):
        f = GossipFilter(max_seq_reorder_window=2)
        assert f.check("replica-a", "n1", 1, _now_ms()) is True
        assert f.check("replica-a", "n2", 2, _now_ms()) is True
        assert f.check("replica-a", "n3", 3, _now_ms()) is True
        stats = f.stats()
        assert stats["max_seq_reorder_window"] == 2


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