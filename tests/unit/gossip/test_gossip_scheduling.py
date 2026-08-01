"""Unit tests for PeerScheduler (ADR-008)."""

from __future__ import annotations

import random

import pytest

from cks_runtime.gossip.scheduling import PeerScheduler, PeerStats


class TestConstruction:
    def test_starts_with_given_peers(self):
        scheduler = PeerScheduler(["http://a", "http://b"])
        assert set(scheduler.peers) == {"http://a", "http://b"}

    def test_rejects_non_positive_base_backoff(self):
        with pytest.raises(ValueError):
            PeerScheduler(["http://a"], base_backoff_s=0)

    def test_rejects_max_backoff_below_base(self):
        with pytest.raises(ValueError):
            PeerScheduler(["http://a"], base_backoff_s=10, max_backoff_s=5)


class TestAddRemovePeer:
    def test_add_peer_starts_tracking_it(self):
        scheduler = PeerScheduler([])
        scheduler.add_peer("http://new")
        assert "http://new" in scheduler.peers

    def test_add_peer_is_idempotent(self):
        scheduler = PeerScheduler(["http://a"])
        scheduler.record_success("http://a")
        scheduler.add_peer("http://a")
        # Stats survive re-adding an already-known peer.
        assert scheduler.stats_for("http://a").successes == 1

    def test_remove_peer_stops_tracking_it(self):
        scheduler = PeerScheduler(["http://a", "http://b"])
        scheduler.remove_peer("http://a")
        assert scheduler.peers == ("http://b",)

    def test_remove_unknown_peer_is_a_no_op(self):
        scheduler = PeerScheduler(["http://a"])
        scheduler.remove_peer("http://never-added")  # should not raise
        assert scheduler.peers == ("http://a",)


class TestEligiblePeers:
    def test_all_peers_eligible_initially(self):
        scheduler = PeerScheduler(["http://a", "http://b"])
        assert set(scheduler.eligible_peers()) == {"http://a", "http://b"}

    def test_failed_peer_excluded_until_backoff_elapses(self):
        scheduler = PeerScheduler(["http://a", "http://b"], base_backoff_s=1.0)
        now = 1_000_000
        scheduler.record_failure("http://a", now_ms=now)

        assert scheduler.eligible_peers(now_ms=now) == ["http://b"]
        # Backoff elapsed (1s later).
        assert set(scheduler.eligible_peers(now_ms=now + 1_100)) == {
            "http://a",
            "http://b",
        }

    def test_success_clears_backoff(self):
        scheduler = PeerScheduler(["http://a"], base_backoff_s=100.0)
        now = 1_000_000
        scheduler.record_failure("http://a", now_ms=now)
        assert scheduler.eligible_peers(now_ms=now) == []

        scheduler.record_success("http://a", now_ms=now)
        assert scheduler.eligible_peers(now_ms=now) == ["http://a"]


class TestBackoffGrowth:
    def test_backoff_doubles_with_consecutive_failures(self):
        scheduler = PeerScheduler(["http://a"], base_backoff_s=1.0, max_backoff_s=1000.0)
        now = 1_000_000

        scheduler.record_failure("http://a", now_ms=now)
        first = scheduler.stats_for("http://a").backoff_until_ms - now
        assert first == 1_000  # 1.0s * 2^0

        scheduler.record_failure("http://a", now_ms=now)
        second = scheduler.stats_for("http://a").backoff_until_ms - now
        assert second == 2_000  # 1.0s * 2^1

        scheduler.record_failure("http://a", now_ms=now)
        third = scheduler.stats_for("http://a").backoff_until_ms - now
        assert third == 4_000  # 1.0s * 2^2

    def test_backoff_caps_at_max(self):
        scheduler = PeerScheduler(["http://a"], base_backoff_s=1.0, max_backoff_s=5.0)
        now = 1_000_000
        for _ in range(10):
            scheduler.record_failure("http://a", now_ms=now)
        assert scheduler.stats_for("http://a").backoff_until_ms - now == 5_000

    def test_success_resets_consecutive_failure_count(self):
        scheduler = PeerScheduler(["http://a"], base_backoff_s=1.0)
        now = 1_000_000
        scheduler.record_failure("http://a", now_ms=now)
        scheduler.record_failure("http://a", now_ms=now)
        scheduler.record_success("http://a", now_ms=now)

        scheduler.record_failure("http://a", now_ms=now)
        # Backoff restarts from the base, not from where it left off.
        assert scheduler.stats_for("http://a").backoff_until_ms - now == 1_000


class TestChoosePeer:
    def test_returns_none_when_no_peers(self):
        scheduler = PeerScheduler([])
        assert scheduler.choose_peer() is None

    def test_returns_none_when_all_peers_backed_off(self):
        scheduler = PeerScheduler(
            ["http://a", "http://b"], base_backoff_s=1000.0, max_backoff_s=2000.0
        )
        now = 1_000_000
        scheduler.record_failure("http://a", now_ms=now)
        scheduler.record_failure("http://b", now_ms=now)
        assert scheduler.choose_peer(now_ms=now) is None

    def test_only_eligible_peer_is_chosen(self):
        scheduler = PeerScheduler(
            ["http://a", "http://b"], base_backoff_s=1000.0, max_backoff_s=2000.0
        )
        now = 1_000_000
        scheduler.record_failure("http://a", now_ms=now)
        assert scheduler.choose_peer(now_ms=now) == "http://b"

    def test_weighted_choice_favors_more_successful_peer(self):
        scheduler = PeerScheduler(["http://reliable", "http://flaky"])
        for _ in range(20):
            scheduler.record_success("http://reliable")
        # Failures timestamped at epoch 0 so their backoff window has
        # long since elapsed by the time choose_peer() below uses the
        # real clock -- both peers stay eligible, so only *weight*
        # (not eligibility) is being exercised here.
        for _ in range(20):
            scheduler.record_failure("http://flaky", now_ms=0)

        rng = random.Random(42)
        choices = [scheduler.choose_peer(rng=rng) for _ in range(200)]
        reliable_count = choices.count("http://reliable")
        flaky_count = choices.count("http://flaky")
        assert reliable_count > flaky_count

    def test_unseen_peer_gets_mid_range_weight(self):
        stats = PeerStats()
        assert stats.weight == pytest.approx(0.5)

    def test_all_successes_approach_weight_one(self):
        stats = PeerStats(successes=1000, failures=0)
        assert stats.weight > 0.99

    def test_all_failures_approach_weight_zero(self):
        stats = PeerStats(successes=0, failures=1000)
        assert stats.weight < 0.01