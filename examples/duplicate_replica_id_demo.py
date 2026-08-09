"""
Duplicate `replica_id` scenario -- the case flagged in the gossip audit
as reproducible-but-unfixed (finding #2): two physically distinct
SQLite files forced to share one `replica_id`, e.g. by baking one
`.db` file (or its `cks_runtime_identity` row) into a shared
deployment template/image instead of letting each installation call
`storage.get_or_create_replica_id()` for itself.

Forces the collision directly, via SQL, rather than actually cloning a
file -- more explicit about exactly what's being reproduced, and
avoids the unrelated WAL naive-copy pitfall (see
`SQLiteStorage`'s class docstring) muddying this specific repro.

BEFORE the DuplicateReplicaIdDetected fix (adapter.py): A and B
committed different objects, gossiped, and silently, asymmetrically
diverged -- A ended up with both objects, B got stuck re-escalating
GossipConflictDetected forever, neither side ever told an operator
*why*. See ADR-008 audit notes for the original repro output.

AFTER the fix: the very first gossip round between A and B refuses to
apply either side's snapshot at all (no fast-forward, no merge probe)
and publishes DuplicateReplicaIdDetected instead -- loud, immediate,
and pointing at the actual cause, rather than a slow-burning silent
divergence discovered later.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import cks

from cks_runtime.events.runtime_event import (
    DuplicateReplicaIdDetected,
    GossipConflictDetected,
)
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.http_transport import GossipServer, HTTPGossipTransport
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime_plugins.cks_core import CksCoreAdapter

SECRET = b"duplicate-replica-id-demo-shared-secret"
BASE_PORT = 8921
SHARED_REPLICA_ID = "11111111-1111-1111-1111-111111111111"


def _force_replica_id(storage: SQLiteStorage, replica_id: str) -> None:
    """Overwrite the durable identity row -- simulates a cloned template."""
    storage._conn.execute("DELETE FROM cks_runtime_identity")
    storage._conn.execute(
        "INSERT INTO cks_runtime_identity (id, replica_id) VALUES (1, ?)",
        (replica_id,),
    )
    storage._conn.commit()


async def make_node(name, port, data_dir, peers):
    storage = SQLiteStorage(str(data_dir / f"{name}.db"))
    _force_replica_id(storage, SHARED_REPLICA_ID)
    runtime = await Runtime.create(core=CksCoreAdapter(), storage=storage)
    assert runtime.replica_id == SHARED_REPLICA_ID, "identity override didn't take"

    adapter = GossipAdapter(runtime, runtime.replica_id)
    server = GossipServer(adapter, secret=SECRET, host="127.0.0.1", port=port)
    transport = HTTPGossipTransport()
    service = GossipService(
        adapter, transport=transport, scheduler=PeerScheduler(peers),
        secret=SECRET, interval_s=0.2, seq_no_counter=server.seq_no_counter,
    )
    return name, runtime, adapter, server, service, transport


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        addrs = [f"http://127.0.0.1:{BASE_PORT + i}" for i in range(2)]
        a_name, a_rt, a_ad, a_srv, a_svc, a_tp = await make_node("A", BASE_PORT, data_dir, [addrs[1]])
        b_name, b_rt, b_ad, b_srv, b_svc, b_tp = await make_node("B", BASE_PORT + 1, data_dir, [addrs[0]])
        nodes = [(a_name, a_rt, a_srv, a_svc, a_tp), (b_name, b_rt, b_srv, b_svc, b_tp)]

        print(f"A replica_id={a_ad.replica_id}")
        print(f"B replica_id={b_ad.replica_id}  (same as A: {a_ad.replica_id == b_ad.replica_id})\n")

        conflicts = []
        duplicates = []
        for name, rt, srv, svc, _tp in nodes:
            rt.events.subscribe(GossipConflictDetected, lambda e, n=name: conflicts.append(n))
            rt.events.subscribe(
                DuplicateReplicaIdDetected,
                lambda e, n=name: duplicates.append((n, e.own_replica_id, e.local_clock, e.remote_clock)),
            )
            await srv.start()

        # Each node independently creates and anchors its OWN session
        # with the same session_id, mirroring how a shared template
        # would have both instances start from an identical genesis.
        structure = cks.KnowledgeStructure(
            [cks.KnowledgeObject(cks.ObjectIdentity(id="root", type="Thing", name="root"))]
        )
        a_session = await a_rt.create_session(structure)
        session_id = a_session.session_id
        GossipAdapter.anchor_genesis(a_session)

        # B independently creates a session under the *same* session_id
        # (both instances started from one shared template, so both
        # "already have" session_id without ever bootstrapping it from
        # each other) -- reuse Runtime's internal id assignment isn't
        # possible across two separate create_session() calls, so
        # anchor B's own genesis session under A's session_id directly
        # via SessionManager.restore(), the same registration path used
        # for a session reloaded from storage at startup.
        b_session = await b_rt.create_session(structure)
        b_session.session_id = session_id
        b_rt._sessions.restore(b_session)
        GossipAdapter.anchor_genesis(b_session)

        for _, _, _, svc, _tp in nodes:
            svc.track_session(session_id)

        # A and B commit different objects independently, exactly as
        # in the original audit repro.
        tx = a_rt.begin_transaction(a_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=a_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-a", type="Thing", name="from-a"))
            )],
        ))
        await a_rt.commit_transaction(tx)

        tx = b_rt.begin_transaction(b_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=b_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-b", type="Thing", name="from-b"))
            )],
        ))
        await b_rt.commit_transaction(tx)

        print("A committed from-a, B committed from-b -- both under replica_id="
              f"{SHARED_REPLICA_ID}\n")

        for _, _, _, svc, _tp in nodes:
            await svc.start()
        await asyncio.sleep(2.5)
        for _, _, srv, svc, tp in nodes:
            await svc.stop()
            await srv.stop()
            await tp.close()

        print(f"GossipConflictDetected fired: {len(conflicts)} time(s) {conflicts}")
        print(f"DuplicateReplicaIdDetected fired: {len(duplicates)} time(s)")
        for n, key, local_clock, remote_clock in duplicates:
            print(f"  [{n}] own_replica_id={key} local_clock={local_clock} remote_clock={remote_clock}")

        print("\nAfter gossip (should NOT have converged -- the guard refuses to merge):")
        for name, rt, _, _, _tp in nodes:
            sess = rt.get_session(session_id)
            ids = sorted(o.identity.id for o in sess.knowledge_structure.objects)
            print(f"  [{name}] объекты: {ids}")

        assert duplicates, "expected DuplicateReplicaIdDetected to fire at least once"
        assert not conflicts, (
            "expected the duplicate-id guard to short-circuit before the merge probe "
            "that would raise GossipConflictDetected"
        )
        print("\nOK: duplicate replica_id was detected and refused, not silently merged.")


if __name__ == "__main__":
    asyncio.run(main())