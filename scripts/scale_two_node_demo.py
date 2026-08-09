"""
2-node gossip scenario, 200+ objects, parallel non-overlapping edits.
Built to probe the fast-forward patch bug found during audit and to
check convergence + timing at scale.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import cks

from cks_runtime.events.runtime_event import GossipConflictDetected
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.http_transport import GossipServer, HTTPGossipTransport
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime_plugins.cks_core import CksCoreAdapter

logging.basicConfig(level=logging.ERROR)

SECRET = b"scale-demo-shared-secret"
BASE_PORT = 8901
N_OBJECTS = 220


async def make_node(name, port, data_dir, peers):
    storage = SQLiteStorage(str(data_dir / f"{name}.db"))
    runtime = await Runtime.create(core=CksCoreAdapter(), storage=storage)
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
        a_name, a_rt, _a_ad, a_srv, a_svc, a_tp = await make_node("A", BASE_PORT, data_dir, [addrs[1]])
        b_name, b_rt, _b_ad, b_srv, b_svc, b_tp = await make_node("B", BASE_PORT + 1, data_dir, [addrs[0]])
        nodes = [(a_name, a_rt, a_srv, a_svc, a_tp), (b_name, b_rt, b_srv, b_svc, b_tp)]

        conflicts = []
        for name, rt, srv, svc, _tp in nodes:
            rt.events.subscribe(GossipConflictDetected, lambda e, n=name: conflicts.append(n))
            await srv.start()

        structure = cks.KnowledgeStructure(
            [cks.KnowledgeObject(cks.ObjectIdentity(id="root", type="Thing", name="root"))]
        )
        a_session = await a_rt.create_session(structure)
        session_id = a_session.session_id
        GossipAdapter.anchor_genesis(a_session)

        for _, _, _, svc, _tp in nodes:
            svc.track_session(session_id)

        for _, _, _, svc, _tp in nodes:
            await svc.start()
        await asyncio.sleep(1.5)
        for _, _, _, svc, _tp in nodes:
            await svc.stop()

        b_session = b_rt.get_session(session_id)
        assert b_session is not None, "B never bootstrapped the session"
        print(f"Bootstrap OK. session={session_id[:8]}")

        # Parallel, field-disjoint bulk writes: A writes obj-a-*, B writes obj-b-*
        async def bulk_write(rt, session, prefix, n):
            tx = rt.begin_transaction(session)
            tx.add_operation(EvolveOperation(
                "evolve", knowledge_structure=session.knowledge_structure,
                evolution=[
                    cks.evolution.AddObject(
                        cks.KnowledgeObject(cks.ObjectIdentity(id=f"{prefix}-{i}", type="Thing", name=f"{prefix}-{i}"))
                    ) for i in range(n)
                ],
            ))
            await rt.commit_transaction(tx)
            print(f"  bulk_write({prefix}) used transaction_id={tx.transaction_id}")

        await bulk_write(a_rt, a_session, "obj-a", N_OBJECTS // 2)
        await bulk_write(b_rt, b_session, "obj-b", N_OBJECTS // 2)
        print(f"A committed {N_OBJECTS//2} objects, B committed {N_OBJECTS//2} objects (disjoint ids).")

        for _, _, _, svc, _tp in nodes:
            await svc.start()
        await asyncio.sleep(3.0)
        for _, _, srv, svc, tp in nodes:
            await svc.stop()
            await srv.stop()
            await tp.close()

        print(f"\nGossipConflictDetected fired: {len(conflicts)} time(s) {conflicts}")
        for name, rt, _, _, _tp in nodes:
            sess = rt.get_session(session_id)
            ids = sorted(o.identity.id for o in sess.knowledge_structure.objects)
            print(f"[{name}] object count={len(ids)} sample={ids[:3]}...{ids[-3:]}")

        # Try to reconstruct every version IN-MEMORY first (patch still live).
        for name, rt, _, _, _tp in nodes:
            sess = rt.get_session(session_id)
            bad = []
            for v in sess.version_history:
                try:
                    sess.get_version_state(v.version_id, rt.core_bridge)
                except Exception as exc:  # noqa: BLE001
                    bad.append((v.version_id, str(exc)))
            print(f"[{name}] (in-memory) versions={len(sess.version_history)} unreconstructable={len(bad)}")

        # Now reload each node's Runtime from its own SQLite file (fresh
        # process restart simulation) and try again -- this round-trips
        # `patch` through storage.patch_json = ... if version.patch else None.
        for name, _, _, _, _tp in nodes:
            storage = SQLiteStorage(str(data_dir / f"{name}.db"))
            fresh_rt = await Runtime.create(core=CksCoreAdapter(), storage=storage)
            sess = fresh_rt.get_session(session_id)
            print(f"[{name}] FULL version history ({len(sess.version_history)} versions):")
            for idx, v in enumerate(sess.version_history):
                patch_len = None if v.patch is None else len(v.patch)
                print(f"    idx={idx} is_snapshot={v.is_snapshot} patch_len={patch_len} tx={v.transaction_id}")


if __name__ == "__main__":
    asyncio.run(main())