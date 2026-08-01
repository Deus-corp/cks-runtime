"""
``SeqNoCounter`` -- the single, persisted ``seq_no`` source SPEC-009
requires ("`seq_no` is a single counter a sending replica shares
across every Session it gossips to every peer", Section 7) but which
the original ``GossipService``/``GossipServer`` implementation didn't
actually provide.

Two bugs, one fix
------------------

1. **Two counters, one identity.** ``GossipService`` (rounds this
   replica initiates) and ``GossipServer`` (replies to rounds a peer
   initiates against this replica) each kept their own private
   in-memory counter (``_seq_no`` / ``_reply_seq_no``), both starting
   at 0, both writing envelopes under the same ``sender_replica_id``.
   The moment two replicas gossip in *both* directions -- the normal
   shape of a mesh, not an edge case -- a peer's ``GossipFilter`` sees
   the same small ``seq_no`` values arrive from two different sources
   under one sender id and rejects the second stream as a replay the
   instant both have sent at least one message.

2. **Not persisted.** Even a single counter was pure in-memory state.
   A replica's durable ``replica_id`` (SPEC-009 Section 4,
   ``storage.get_or_create_replica_id()``) survives a process restart
   by design; its ``seq_no`` did not. A peer's ``GossipFilter`` state
   is itself in-memory and remembers the highest ``seq_no`` it has
   ever accepted from that ``replica_id`` for as long as that peer
   process runs. A replica resuming at ``seq_no=1`` after a restart is
   therefore rejected by every peer that remembers a higher value from
   it -- permanently, since nothing calls ``GossipFilter.reset()``
   automatically anywhere in this package.

This module fixes both with one mechanism: ``next()`` always
reads-increments-writes a small JSON file (keyed by ``replica_id``,
alongside the persisted gossip secret and identity) rather than only
seeding an in-memory counter once at construction. That makes the
*file* the single source of truth for one ``replica_id``'s ``seq_no``
stream:

- Two independently constructed ``SeqNoCounter``\\ s for the same
  ``replica_id`` (e.g. one held by ``GossipService``, one by
  ``GossipServer``, in the same process -- see fix 1 above) never
  collide, because every single ``next()`` call, not just the first,
  reconciles against whatever the other one most recently wrote.
- A replica resuming after a restart continues past whatever it last
  persisted (fix 2), as long as the same data directory is available
  (``CKS_RUNTIME_DATA_DIR``, matching ``secret.py``'s own resolution
  rule).

Deliberately not a module-level singleton, mirroring ``secret.py``'s
own stated reasoning: callers hold a ``SeqNoCounter`` explicitly
(``GossipService``/``GossipServer`` build one from ``adapter.replica_id``
when the caller doesn't supply one) rather than this module reaching
for global state, which keeps construction easy to override and to
test. Never raises on a non-writable filesystem -- same fallback as
``load_secret``: an unpersisted counter still works for the current
process, it just forfeits fix 2 (and, if two roles for the same
replica_id can't share a writable path, fix 1) until the filesystem is
available again.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from cks_runtime.gossip.secret import default_secret_path

_SEQ_NO_FILENAME = "gossip_seq_no"


def default_seq_no_path() -> Path:
    """
    Where the persisted ``seq_no`` state lives absent an explicit
    ``path``.

    Same parent directory as ``secret.default_secret_path()``
    (``CKS_RUNTIME_DATA_DIR``, or ``~/.cks_runtime``) -- both are
    per-installation gossip state, and a deployment that already
    isolates one replica's secret from another's (e.g. distinct
    ``CKS_RUNTIME_DATA_DIR`` values for multiple local replicas, per
    ``secret.py``) gets the same isolation for ``seq_no`` for free.
    """
    return default_secret_path().with_name(_SEQ_NO_FILENAME)


class SeqNoCounter:
    """
    A monotonically increasing ``seq_no`` source for one
    ``replica_id``, persisted (by default) across both restarts and
    multiple independently constructed instances in the same process.

    Share the *same* ``replica_id`` and ``path`` -- the default when
    neither is overridden -- between everything that signs a
    ``GossipEnvelope`` under that ``sender_replica_id``. Explicitly
    passing one shared instance to both ``GossipService`` and
    ``GossipServer`` works too and skips the file round-trip each
    ``next()`` call otherwise does to reconcile against the other
    role, but is not required for correctness: two separate instances
    pointed at the same persisted state never issue a colliding value
    (see module docstring).
    """

    def __init__(
        self,
        replica_id: str,
        *,
        path: Path | None = None,
        persist: bool = True,
        start: int = 0,
    ) -> None:
        if not str(replica_id).strip():
            raise ValueError("replica_id must be non-empty.")
        if start < 0:
            raise ValueError(f"start must be >= 0, got {start!r}.")

        self._replica_id = str(replica_id)
        self._persist = persist
        self._path = path if path is not None else default_seq_no_path()
        self._lock = threading.Lock()

        initial = self._read_one(self._replica_id) if persist else 0
        self._value = max(start, initial)

    @property
    def replica_id(self) -> str:
        return self._replica_id

    @property
    def current(self) -> int:
        """The last ``seq_no`` this instance has handed out (0 before the first ``next()``)."""
        with self._lock:
            return self._value

    def next(self) -> int:
        """
        Return the next ``seq_no`` to sign an envelope with.

        When ``persist`` is enabled (the default), this reconciles
        against the persisted file *first* -- not just at
        construction -- so a sibling ``SeqNoCounter`` for the same
        ``replica_id`` that has advanced further (a different role in
        this same process, or this same replica in a prior run) is
        always respected, never overwritten backwards.
        """
        with self._lock:
            floor = self._value
            if self._persist:
                all_values = self._read_all()
                floor = max(floor, all_values.get(self._replica_id, 0))
            else:
                all_values = {}

            self._value = floor + 1

            if self._persist:
                all_values[self._replica_id] = self._value
                self._write_all(all_values)

            return self._value

    # ------------------------------------------------------------------
    # Persistence (JSON, keyed by replica_id -- see module docstring)
    # ------------------------------------------------------------------

    def _read_all(self) -> dict[str, int]:
        try:
            raw = self._path.read_text()
        except (FileNotFoundError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): int(v)
            for k, v in data.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def _read_one(self, replica_id: str) -> int:
        return self._read_all().get(replica_id, 0)

    def _write_all(self, data: dict[str, int]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data))
        except OSError:
            # Never raises on a non-writable filesystem, matching
            # secret.load_secret -- this process keeps working from
            # its in-memory self._value, it just can't coordinate with
            # a sibling instance or a future restart until the
            # filesystem is writable again.
            pass