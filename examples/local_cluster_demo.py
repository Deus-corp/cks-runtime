"""
Локальный gossip-кластер из 3 узлов без Docker -- v2, после патча
Genesis Block (ADR-008, cks-runtime 1.30.0).

В отличие от первой версии этого скрипта, сессия здесь реально
создаётся только на ОДНОМ узле (supervisor) и распространяется на
critic/worker через настоящий HTTP-бутстрап (_bootstrap_remote_session),
а не руками через RuntimeSession(...)+_sessions.restore(). Это честнее
отражает то, как это будет работать в реальном деплое: session_id не
существует ниоткуда заранее, его должен кто-то создать первым.

Каждый узел -- отдельный Runtime + отдельная SQLite-база + отдельный
GossipServer на своём localhost-порту. Всё в одном Python-процессе --
никакого Docker, никаких пересборок.

Запуск: python local_cluster_demo.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import cks

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.events.runtime_event import GossipConflictDetected
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.http_transport import GossipServer, HTTPGossipTransport
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.storage.sqlite_storage import SQLiteStorage

SECRET = b"local-demo-shared-secret"  # в реальном деплое -- из секрет-хранилища, один на всех узлов
BASE_PORT = 8801
NODE_NAMES = ["supervisor", "critic", "worker"]


class Node:
    def __init__(self, name: str, runtime: Runtime, adapter: GossipAdapter,
                 server: GossipServer, service: GossipService,
                 transport: HTTPGossipTransport) -> None:
        self.name = name
        self.runtime = runtime
        self.adapter = adapter
        self.server = server
        self.service = service
        # Kept explicitly (rather than reaching into GossipService's
        # private ``_transport``) so callers can close the pooled
        # aiohttp.ClientSession this demo opened -- GossipService
        # treats the transport as caller-owned/possibly-shared and
        # never closes it itself, so without this the demo leaked an
        # "Unclosed client session" per node on every run.
        self.transport = transport

    def session(self, session_id: str):
        return self.runtime.get_session(session_id)


async def make_node(name: str, port: int, data_dir: Path, peers: list[str]) -> Node:
    storage = SQLiteStorage(str(data_dir / f"{name}.db"))
    runtime = await Runtime.create(core=CksCoreAdapter(), storage=storage)
    adapter = GossipAdapter(runtime, runtime.replica_id)  # durable identity (ADR-008 §1)

    server = GossipServer(adapter, secret=SECRET, host="127.0.0.1", port=port)
    transport = HTTPGossipTransport()
    service = GossipService(
        adapter,
        transport=transport,
        scheduler=PeerScheduler(peers),
        secret=SECRET,
        interval_s=0.3,
        seq_no_counter=server.seq_no_counter,
    )
    return Node(name, runtime, adapter, server, service, transport)


async def run_rounds(nodes: list[Node], seconds: float) -> None:
    for node in nodes:
        await node.service.start()
    await asyncio.sleep(seconds)
    for node in nodes:
        await node.service.stop()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        addrs = [f"http://127.0.0.1:{BASE_PORT + i}" for i in range(len(NODE_NAMES))]

        nodes = [
            await make_node(name, BASE_PORT + i, data_dir,
                             peers=[a for j, a in enumerate(addrs) if j != i])
            for i, name in enumerate(NODE_NAMES)
        ]
        sup, critic, worker = nodes

        conflicts: list[str] = []
        for node in nodes:
            node.runtime.events.subscribe(
                GossipConflictDetected,
                lambda e, n=node.name: conflicts.append(n),
            )
            await node.server.start()
            print(f"[{node.name}] слушает на порту {node.server._port}, "
                  f"replica_id={node.adapter.replica_id[:8]}")

        # --- Только supervisor реально создаёт сессию. -----------------
        structure = cks.KnowledgeStructure(
            [cks.KnowledgeObject(cks.ObjectIdentity(id="root", type="Thing", name="root"))]
        )
        sup_session = await sup.runtime.create_session(structure)
        session_id = sup_session.session_id
        # Единственное место, где это нужно вызывать вручную -- узел,
        # создавший сессию локально, не через gossip-бутстрап.
        GossipAdapter.anchor_genesis(sup_session)

        for node in nodes:
            node.service.track_session(session_id)

        print(f"\nСессия {session_id[:8]} создана на supervisor. "
              f"critic и worker её ещё не видели.\n")

        # --- Фаза 1: даём supervisor реально разнести её по HTTP. ------
        await run_rounds(nodes, seconds=2.0)

        for node in (critic, worker):
            assert node.session(session_id) is not None, \
                f"{node.name} не получил сессию за отведённое время"
        print("critic и worker забутстрапились через настоящий "
              "_bootstrap_remote_session (не руками).\n")

        # --- Фаза 2: supervisor и worker правят независимо. -------------
        tx = sup.runtime.begin_transaction(sup_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=sup_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-supervisor", type="Thing", name="from-supervisor"))
            )],
        ))
        await sup.runtime.commit_transaction(tx)

        worker_session = worker.session(session_id)
        tx = worker.runtime.begin_transaction(worker_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=worker_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-worker", type="Thing", name="from-worker"))
            )],
        ))
        await worker.runtime.commit_transaction(tx)

        print("Расходятся: supervisor добавил from-supervisor, "
              "worker -- from-worker. critic не видел ни одной правки.\n")

        # --- Фаза 3: gossip должен свести все три состояния. ------------
        await run_rounds(nodes, seconds=2.0)

        for node in nodes:
            await node.server.stop()
            await node.transport.close()

        print(f"GossipConflictDetected сработал: {len(conflicts)} раз(а) {conflicts}\n")
        print("После gossip:")
        for node in nodes:
            session = node.session(session_id)
            ids = sorted(o.identity.id for o in session.knowledge_structure.objects)
            print(f"  [{node.name}] объекты: {ids}")


if __name__ == "__main__":
    asyncio.run(main())
