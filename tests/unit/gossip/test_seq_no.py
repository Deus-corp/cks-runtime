"""
Unit tests for ``SeqNoCounter`` (see ``seq_no.py``'s module docstring
for the three bugs it fixes: two independent counters for one
``replica_id``, no persistence across restarts, and no cross-process
locking around the persisted file).
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from cks_runtime.gossip.seq_no import _HAS_FLOCK, SeqNoCounter, default_seq_no_path


def _issue_seq_nos(args: tuple[str, int]) -> list[int]:
    """
    Module-level (picklable) worker: build a fresh ``SeqNoCounter`` for
    ``replica-race`` in this process and issue ``n`` seq_nos from it.

    Deliberately a brand-new ``SeqNoCounter`` per worker process (not
    one shared/pickled instance) -- the scenario under test is
    *independent processes* racing on the same persisted file, exactly
    like a real deployment where each process constructs its own
    ``SeqNoCounter`` from the same durable ``replica_id``/data
    directory.
    """
    path_str, n = args
    counter = SeqNoCounter("replica-race", path=Path(path_str))
    return [counter.next() for _ in range(n)]


class TestBasicIncrement:
    def test_starts_at_one(self, tmp_path: Path):
        counter = SeqNoCounter("replica-a", path=tmp_path / "seq_no.json")
        assert counter.next() == 1

    def test_strictly_increasing(self, tmp_path: Path):
        counter = SeqNoCounter("replica-a", path=tmp_path / "seq_no.json")
        values = [counter.next() for _ in range(5)]
        assert values == [1, 2, 3, 4, 5]

    def test_current_reflects_last_issued(self, tmp_path: Path):
        counter = SeqNoCounter("replica-a", path=tmp_path / "seq_no.json")
        assert counter.current == 0
        counter.next()
        counter.next()
        assert counter.current == 2

    def test_rejects_empty_replica_id(self, tmp_path: Path):
        with pytest.raises(ValueError):
            SeqNoCounter("", path=tmp_path / "seq_no.json")

    def test_rejects_negative_start(self, tmp_path: Path):
        with pytest.raises(ValueError):
            SeqNoCounter("replica-a", path=tmp_path / "seq_no.json", start=-1)


class TestPersistenceAcrossRestart:
    """Reproduces the restart scenario from the code review, fixed."""

    def test_second_instance_continues_past_first(self, tmp_path: Path):
        path = tmp_path / "seq_no.json"

        # "Before restart": this replica sends 50 gossip rounds.
        before_restart = SeqNoCounter("replica-a", path=path)
        for _ in range(50):
            before_restart.next()
        assert before_restart.current == 50

        # "Restart": a brand-new process, same durable replica_id,
        # same data directory -- exactly what a deployment following
        # SPEC-009 Section 4's advice (source replica_id from
        # storage.get_or_create_replica_id()) would have.
        after_restart = SeqNoCounter("replica-a", path=path)

        # Before the fix this would start over at 1, which a peer
        # that remembers seq_no=50 from "replica-a" would reject as
        # non-monotonic -- permanently, since nothing calls
        # GossipFilter.reset() automatically anywhere in this package.
        assert after_restart.next() == 51

    def test_disabling_persist_does_not_survive_restart(self, tmp_path: Path):
        """`persist=False` is an explicit opt-out (e.g. for tests); it should not
        pretend to survive a restart."""
        path = tmp_path / "seq_no.json"
        before = SeqNoCounter("replica-a", path=path, persist=False)
        before.next()
        before.next()

        after = SeqNoCounter("replica-a", path=path, persist=False)
        assert after.next() == 1

    def test_unwritable_path_degrades_gracefully(self, tmp_path: Path):
        """A non-writable filesystem must not raise -- same fallback as
        secret.load_secret."""
        unwritable = tmp_path / "no" / "such" / "parent-that-cannot-be-created"
        counter = SeqNoCounter("replica-a", path=unwritable)
        # Still usable in-process even though nothing could be persisted.
        assert counter.next() == 1
        assert counter.next() == 2


class TestNoCollisionBetweenSiblingInstances:
    """
    Reproduces the "GossipService and GossipServer for the same
    replica_id collide" scenario from the code review, fixed: two
    *independently constructed* SeqNoCounters sharing a replica_id and
    path must never hand out the same value, in either construction
    order.
    """

    def test_two_instances_constructed_before_any_use_never_collide(self, tmp_path: Path):
        path = tmp_path / "seq_no.json"

        # Mirrors GossipService and GossipServer both being built at
        # startup, before either has sent anything.
        service_side = SeqNoCounter("replica-x", path=path)
        server_side = SeqNoCounter("replica-x", path=path)

        issued = set()
        for _ in range(20):
            a = service_side.next()
            b = server_side.next()
            assert a not in issued, "GossipService side re-issued a used seq_no"
            assert b not in issued, "GossipServer side re-issued a used seq_no"
            issued.add(a)
            issued.add(b)

        assert len(issued) == 40

    def test_late_constructed_sibling_picks_up_where_first_left_off(self, tmp_path: Path):
        path = tmp_path / "seq_no.json"

        service_side = SeqNoCounter("replica-x", path=path)
        for _ in range(10):
            service_side.next()

        # GossipServer only gets built (and only ever replies) later --
        # it must not reuse 1..10.
        server_side = SeqNoCounter("replica-x", path=path)
        assert server_side.next() == 11

    def test_different_replica_ids_do_not_interfere(self, tmp_path: Path):
        path = tmp_path / "seq_no.json"
        replica_a = SeqNoCounter("replica-a", path=path)
        replica_b = SeqNoCounter("replica-b", path=path)

        assert replica_a.next() == 1
        assert replica_b.next() == 1
        assert replica_a.next() == 2
        assert replica_b.next() == 2


class TestDefaultPath:
    def test_default_path_respects_data_dir_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CKS_RUNTIME_DATA_DIR", str(tmp_path))
        assert default_seq_no_path() == tmp_path / "gossip_seq_no"

    def test_default_path_shares_parent_with_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from cks_runtime.gossip.secret import default_secret_path

        monkeypatch.setenv("CKS_RUNTIME_DATA_DIR", str(tmp_path))
        assert default_seq_no_path().parent == default_secret_path().parent


class TestCrossProcessLocking:
    """
    Reproduces the "two OS processes sharing one replica_id/path both
    compute the same next value" race from the code review, fixed by
    wrapping ``next()``'s critical section in an ``fcntl.flock``
    (``seq_no.py``'s "third bug"). Uses real, separate OS processes
    (not threads) -- a ``threading.Lock`` alone cannot protect against
    this race by construction, so only a multi-process reproduction
    actually exercises the fix.
    """

    @pytest.mark.skipif(
        not _HAS_FLOCK, reason="fcntl-based cross-process locking is POSIX-only"
    )
    def test_concurrent_processes_never_issue_duplicate_seq_no(self, tmp_path: Path):
        path = tmp_path / "seq_no.json"
        n_workers = 8
        calls_per_worker = 25

        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            results = list(
                pool.map(
                    _issue_seq_nos,
                    [(str(path), calls_per_worker)] * n_workers,
                )
            )

        all_issued = [seq for worker_result in results for seq in worker_result]

        assert len(all_issued) == n_workers * calls_per_worker
        assert len(set(all_issued)) == len(all_issued), (
            "duplicate seq_no issued across processes -- cross-process "
            "lock failed to serialize next()"
        )