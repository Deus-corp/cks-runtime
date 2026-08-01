"""
Локальный gossip-кластер из 3 узлов без Docker.

Каждый узел -- это отдельный Runtime + отдельная SQLite-база +
отдельный GossipServer на своём localhost-порту. Всё в одном
Python-процессе и одном asyncio event loop -- никаких контейнеров,
никаких пересборок: правишь код -> Ctrl+C -> `python local_cluster_demo.py`.

Запуск: python local_cluster_demo.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

import cks

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.http_transport import GossipServer, HTTPGossipTransport
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime_plugins.cks_core import CksCoreAdapter

SECRET = b"local-demo-shared-secret"  # в реальном деплое -- из секрет-хранилища, один на всех узлов
BASE_PORT = 8801
NODE_NAMES = ["supervisor", "critic", "worker"]


async def make_node(name: str, port: int, data_dir: Path, peers: list[str]):
    db_path = str(data_dir / f"{name}.db")
    storage = SQLiteStorage(db_path)
    replica_id = storage.get_or_create_replica_id()  # переживает рестарт узла

    runtime = await Runtime.create(core=CksCoreAdapter(), storage=storage)
    adapter = GossipAdapter(runtime, replica_id)

    server = GossipServer(adapter, secret=SECRET, host="127.0.0.1", port=port)
    service = GossipService(
        adapter,
        transport=HTTPGossipTransport(),
        scheduler=PeerScheduler(peers),
        secret=SECRET,
        interval_s=0.3,
        seq_no_counter=server.seq_no_counter,  # один источник seq_no на узел (SPEC-009 §7)
    )
    return name, runtime, adapter, server, service


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        addrs = [f"http://127.0.0.1:{BASE_PORT + i}" for i in range(len(NODE_NAMES))]

        nodes = []
        for i, name in enumerate(NODE_NAMES):
            peers = [a for j, a in enumerate(addrs) if j != i]  # полносвязная топология для демо
            nodes.append(await make_node(name, BASE_PORT + i, data_dir, peers))

        from cks_runtime.events.runtime_event import GossipConflictDetected

        for name, runtime, adapter, server, service in nodes:
            await server.start()
            print(f"[{name}] слушает на порту {server._port}, replica_id={adapter.replica_id[:8]}")

            def make_handler(node_name: str):
                async def _on_conflict(event) -> None:
                    print(f"  [debug event] {node_name}: GossipConflictDetected conflicts={event.conflicts}")
                return _on_conflict

            runtime.events.subscribe(GossipConflictDetected, make_handler(name))

        # Одна и та же сессия существует на всех трёх узлах -- как будто её
        # синхронизировали заранее (bootstrap кластера).
        def base_structure() -> cks.KnowledgeStructure:
            return cks.KnowledgeStructure(
                [cks.KnowledgeObject(cks.ObjectIdentity(id="root", type="Thing", name="root"))]
            )

        first_runtime = nodes[0][1]
        first_session = await first_runtime.create_session(base_structure())
        session_id = first_session.session_id
        node_sessions = {NODE_NAMES[0]: first_session}

        for name, runtime, adapter, server, service in nodes[1:]:
            session = RuntimeSession(
                knowledge_structure=base_structure(), session_id=session_id
            )
            session.metadata["node_id"] = str(uuid4())
            runtime._sessions.restore(session)
            await runtime.storage.save_session(session)
            node_sessions[name] = session

        for name, runtime, adapter, server, service in nodes:
            service._session_ids.append(session_id)

        # Supervisor и Worker независимо друг от друга правят граф --
        # это ровно та ситуация без единой точки отказа из пункта 2.
        sup_runtime = nodes[0][1]
        sup_session = node_sessions["supervisor"]
        tx = sup_runtime.begin_transaction(sup_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=sup_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-supervisor", type="Thing", name="from-supervisor"))
            )],
        ))
        await sup_runtime.commit_transaction(tx)

        worker_runtime = nodes[2][1]
        worker_session = node_sessions["worker"]
        tx = worker_runtime.begin_transaction(worker_session)
        tx.add_operation(EvolveOperation(
            "evolve", knowledge_structure=worker_session.knowledge_structure,
            evolution=[cks.evolution.AddObject(
                cks.KnowledgeObject(cks.ObjectIdentity(id="from-worker", type="Thing", name="from-worker"))
            )],
        ))
        await worker_runtime.commit_transaction(tx)

        print("\nДо gossip: supervisor и worker разошлись, critic не видел ни одной правки.\n")

        for name, runtime, adapter, server, service in nodes:
            await service.start()

        await asyncio.sleep(6.0)  # ~20 anti-entropy раундов на interval_s=0.3

        for name, runtime, adapter, server, service in nodes:
            for peer, stats in service._scheduler._stats.items():
                print(f"  [debug] {name} -> {peer}: successes={stats.successes} failures={stats.failures}")
            await service.stop()
            await server.stop()

        print("После gossip:")
        for name, runtime, adapter, server, service in nodes:
            session = await runtime.storage.load_session(session_id)
            ids = sorted(o.identity.id for o in session.knowledge_structure.objects)
            print(f"  [{name}] объекты: {ids}")


if __name__ == "__main__":
    asyncio.run(main())
