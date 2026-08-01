"""
Integration tests for the real aiohttp gossip transport (ADR-008):
``HTTPGossipTransport``, ``GossipServer``, and ``HTTPPeerDiscovery``,
exercised against genuine aiohttp servers bound to localhost -- not
the in-process ``FakeTransport`` ``test_gossip_transport.py`` uses.

This covers what only a real network round trip can: wire
(de)serialization through ``GossipEnvelope.to_dict()``/``from_dict()``
over actual JSON bodies, the signature/replay-filter checks
``GossipServer._handle_gossip`` enforces on an HTTP request (not a
direct Python call), the ``/gossip/peers`` route
``HTTPPeerDiscovery`` talks to, and a ``GossipService`` round wired
end-to-end against a live peer.

Referenced by ``test_gossip_discovery.py``'s module docstring ("The
HTTP wire implementation ... is covered separately in
test_http_transport.py, against real aiohttp servers") and by
``http_transport.py``'s own module docstring.
"""

from __future__ import annotations

import copy
import socket
from uuid import uuid4

import cks
import pytest

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.discovery import merge_discovered_peers
from cks_runtime.gossip.filter import GossipFilter
from cks_runtime.gossip.http_transport import (
    GossipServer,
    HTTPGossipTransport,
    HTTPPeerDiscovery,
)
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.gossip.transport import (
    GossipTransportError,
    gossip_exchange_over_transport,
)
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio

SECRET = b"shared-http-integration-secret"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_gossip_transport.py / test_gossip_adapter.py, plus
# real-server bootstrapping)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """
    Ask the OS for a currently-unused localhost TCP port.

    Bind-then-immediately-close: there's an inherent TOCTOU race (the
    port could theoretically be grabbed by something else before
    ``GossipServer.start()`` binds it), acceptable here the same way
    it is in any test suite that needs a real ephemeral port --
    ``aiohttp.test_utils`` uses the identical approach internally.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i)) for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def _add(obj_id: str) -> cks.evolution.AddObject:
    return cks.evolution.AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id=obj_id, type="Thing", name=obj_id))
    )


async def _evolve(runtime: Runtime, session: RuntimeSession, operations: list) -> None:
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve", knowledge_structure=session.knowledge_structure, evolution=operations
        )
    )
    await runtime.commit_transaction(tx)


async def _paired_replicas() -> tuple[Runtime, Runtime, str]:
    """Two independent Runtimes tracking one shared session_id, no shared history."""
    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))

    session_b = RuntimeSession(
        knowledge_structure=make_structure(["root"]), session_id=session_a.session_id
    )
    session_b.metadata["node_id"] = str(uuid4())
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id


async def _paired_replicas_with_shared_base() -> tuple[Runtime, Runtime, str]:
    """
    Like ``_paired_replicas``, but both sides also carry the same
    genesis ``RuntimeVersion`` locally, so a three-way merge has a
    real base to resolve against instead of escalating on the very
    first round. See ``test_gossip_transport.py``'s helper of the
    same name for the full rationale -- identical here, just against
    a real HTTP round trip instead of ``FakeTransport``.
    """
    from cks_runtime.versioning.version import RuntimeVersion

    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))

    genesis = RuntimeVersion(
        session_id=session_a.session_id,
        transaction_id="genesis",
        knowledge_structure=make_structure(["root"]),
        metadata={},
    )
    session_a.version_history.append(genesis)
    session_a.parent_version_id = genesis.version_id
    await runtime_a.storage.save_session(session_a)

    session_b = RuntimeSession(
        knowledge_structure=copy.deepcopy(session_a.knowledge_structure),
        session_id=session_a.session_id,
        parent_version_id=genesis.version_id,
    )
    session_b.metadata["node_id"] = str(uuid4())
    session_b.version_history.append(genesis)
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id


class _RunningServer:
    """A started ``GossipServer`` plus the base URL to reach it at."""

    def __init__(self, server: GossipServer, url: str) -> None:
        self.server = server
        self.url = url


async def _start_server(
    adapter: GossipAdapter,
    *,
    gossip_filter: GossipFilter | None = None,
    known_peers=None,
    self_address: str | None = None,
) -> _RunningServer:
    port = _free_port()
    server = GossipServer(
        adapter,
        secret=SECRET,
        host="127.0.0.1",
        port=port,
        gossip_filter=gossip_filter,
        known_peers=known_peers,
        self_address=self_address,
    )
    await server.start()
    return _RunningServer(server, f"http://127.0.0.1:{port}")


# ---------------------------------------------------------------------------
# Session exchange over a real HTTP round trip
# ---------------------------------------------------------------------------


class TestHTTPGossipRoundTrip:
    async def test_fast_forward_converges_over_real_http(self):
        """
        Only B has committed -- A's exchange against a live B server
        must fast-forward A's local state, with the JSON envelope
        actually crossing a real socket both ways.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        running_b = await _start_server(adapter_b)
        transport = HTTPGossipTransport()
        try:
            result = await gossip_exchange_over_transport(
                session_id, adapter_a, transport, running_b.url, secret=SECRET, seq_no=1
            )
            assert result is True
            assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
                "root",
                "b",
            }
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_three_way_merge_converges_both_sides_over_real_http(self):
        """
        Both replicas committed independently from a shared base.
        One push-pull round (A calls B) must leave *both* sides
        converged: A merges B's snapshot locally, and the reply B
        sends back is A's now-merged snapshot, which B merges in turn
        server-side inside ``_handle_gossip``.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas_with_shared_base()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)
        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        running_b = await _start_server(adapter_b)
        transport = HTTPGossipTransport()
        try:
            result = await gossip_exchange_over_transport(
                session_id, adapter_a, transport, running_b.url, secret=SECRET, seq_no=1
            )
            assert result is True
            ids_a = {o.identity.id for o in session_a.knowledge_structure.objects}
            ids_b = {o.identity.id for o in session_b.knowledge_structure.objects}
            assert ids_a == {"root", "a", "b"}
            assert ids_b == {"root", "a", "b"}
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_bootstraps_a_brand_new_session_on_the_peer_over_real_http(self):
        """
        A live peer that has never tracked ``session_a.session_id``
        at all does *not* reply 404 -- ``GossipAdapter.apply_remote_session``
        bootstraps an unseen session_id as new local state (see its
        module docstring's "ADR-008 status update (bootstrap)"), so
        the server always has *something* to reply with after
        applying a well-formed, correctly signed envelope. This
        confirms that adopt-on-first-contact actually happens over a
        real socket, not just in-process (``test_gossip_adapter.py``
        already covers the in-process bootstrap mechanics).
        """
        runtime_a = await Runtime.create(core=CksCoreAdapter())
        runtime_b = await Runtime.create(core=CksCoreAdapter())
        session_a = await runtime_a.create_session(make_structure(["root"]))

        adapter_b = GossipAdapter(runtime_b, "replica-b")
        running_b = await _start_server(adapter_b)
        transport = HTTPGossipTransport()
        try:
            reply = await transport.exchange(
                running_b.url,
                _envelope_for(session_a, "replica-a", seq_no=1),
            )
            assert reply is not None
            assert reply.session_id == session_a.session_id

            bootstrapped = runtime_b.get_session(session_a.session_id)
            assert bootstrapped is not None
            assert {o.identity.id for o in bootstrapped.knowledge_structure.objects} == {
                "root"
            }
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_unreachable_peer_raises_gossip_transport_error(self):
        """
        Nobody listening on this port at all -- the client transport
        must wrap the connection failure in ``GossipTransportError``,
        not let a raw ``aiohttp.ClientError`` escape.
        """
        runtime_a = await Runtime.create(core=CksCoreAdapter())
        session_a = await runtime_a.create_session(make_structure(["root"]))
        transport = HTTPGossipTransport()
        dead_port = _free_port()  # freed immediately, nothing bound to it

        try:
            with pytest.raises(GossipTransportError):
                await transport.exchange(
                    f"http://127.0.0.1:{dead_port}",
                    _envelope_for(session_a, "replica-a", seq_no=1),
                )
        finally:
            await transport.close()

    async def test_signature_verification_failure_over_real_http(self):
        """
        A server signing/verifying with a different secret than the
        client must reject the request with 401, surfaced client-side
        as ``GossipTransportError`` -- and must not have applied
        anything locally.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        await _evolve(runtime_a, session_a, [_add("a")])

        adapter_b = GossipAdapter(runtime_b, "replica-b")
        running_b = await _start_server(adapter_b)  # server checks against SECRET

        transport = HTTPGossipTransport()
        try:
            wrong_secret_envelope = _envelope_for(
                session_a, "replica-a", seq_no=1, secret=b"not-the-shared-secret"
            )
            with pytest.raises(GossipTransportError):
                await transport.exchange(running_b.url, wrong_secret_envelope)

            # Local state on B (which, per _paired_replicas, already
            # tracks this session_id at "root" only) must be
            # untouched -- the forged envelope was rejected before
            # apply_remote_session ever ran, so "a" must not appear.
            unaffected = runtime_b.get_session(session_id)
            assert unaffected is not None
            assert {o.identity.id for o in unaffected.knowledge_structure.objects} == {
                "root"
            }
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_replay_filter_rejects_a_resent_envelope_over_real_http(self):
        """
        The same signed envelope (identical nonce + seq_no) POSTed to
        a live server twice: the first request must be accepted, the
        second rejected by ``GossipServer``'s ``GossipFilter`` with
        HTTP 409, which the client surfaces as
        ``GossipTransportError`` -- exercising the *server's* replay
        filter over the wire, not the client-side filter
        ``test_gossip_transport.py`` already covers.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        await _evolve(runtime_a, session_a, [_add("a")])

        adapter_b = GossipAdapter(runtime_b, "replica-b")
        running_b = await _start_server(adapter_b, gossip_filter=GossipFilter())

        transport = HTTPGossipTransport()
        try:
            envelope = _envelope_for(session_a, "replica-a", seq_no=1)

            first_reply = await transport.exchange(running_b.url, envelope)
            assert first_reply is not None
            assert {
                o.identity.id
                for o in runtime_b.get_session(session_id).knowledge_structure.objects
            } == {"root", "a"}

            with pytest.raises(GossipTransportError):
                await transport.exchange(running_b.url, envelope)
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_malformed_reply_raises_gossip_transport_error(self):
        """
        A peer that replies 200 OK with a body that isn't a valid
        envelope (missing required fields) must surface as
        ``GossipTransportError`` client-side, not an uncaught
        ``KeyError`` -- ``GossipServer`` itself never produces such a
        reply, so this stands up a minimal real aiohttp app of its
        own to produce one, rather than asserting on
        ``GossipEnvelope.from_dict`` directly.
        """
        from aiohttp import web

        async def _malformed_reply(request: web.Request) -> web.Response:
            return web.json_response({"not": "a valid envelope"})

        app = web.Application()
        app.router.add_post("/gossip/{session_id}", _malformed_reply)
        runner = web.AppRunner(app)
        await runner.setup()
        port = _free_port()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()

        runtime_a = await Runtime.create(core=CksCoreAdapter())
        session_a = await runtime_a.create_session(make_structure(["root"]))
        transport = HTTPGossipTransport()
        try:
            with pytest.raises(GossipTransportError):
                await transport.exchange(
                    f"http://127.0.0.1:{port}",
                    _envelope_for(session_a, "replica-a", seq_no=1),
                )
        finally:
            await transport.close()
            await runner.cleanup()


def _envelope_for(session: RuntimeSession, replica_id: str, *, seq_no: int, secret: bytes = SECRET):
    from cks_runtime.gossip.envelope import GossipEnvelope

    return GossipEnvelope.from_session(
        session, sender_replica_id=replica_id, seq_no=seq_no, secret=secret
    )


# ---------------------------------------------------------------------------
# Peer discovery over a real HTTP round trip
# ---------------------------------------------------------------------------


class TestHTTPPeerDiscoveryRoundTrip:
    async def test_fetches_peers_from_a_real_server(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-b")
        running = await _start_server(
            adapter,
            known_peers=["http://peer-1", "http://peer-2"],
            self_address="http://self-b",
        )

        discovery = HTTPPeerDiscovery()
        try:
            peers = await discovery.fetch_peers(running.url)
            # self_address is always included, first, per GossipServer._handle_peers.
            assert peers[0] == "http://self-b"
            assert set(peers) == {"http://self-b", "http://peer-1", "http://peer-2"}
        finally:
            await discovery.close()
            await running.server.stop()

    async def test_empty_known_peers_still_reports_self_address(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-b")
        running = await _start_server(adapter, self_address="http://self-b")

        discovery = HTTPPeerDiscovery()
        try:
            peers = await discovery.fetch_peers(running.url)
            assert peers == ["http://self-b"]
        finally:
            await discovery.close()
            await running.server.stop()

    async def test_callable_known_peers_is_evaluated_live(self):
        """
        ``known_peers`` as a callable must be re-evaluated on every
        request (see ``GossipServer`` docstring), so a peer set that
        changes between two requests is reflected without restarting
        the server.
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-b")
        live_peers = ["http://peer-1"]
        running = await _start_server(adapter, known_peers=lambda: list(live_peers))

        discovery = HTTPPeerDiscovery()
        try:
            first = await discovery.fetch_peers(running.url)
            assert set(first) == {"http://peer-1"}

            live_peers.append("http://peer-2")
            second = await discovery.fetch_peers(running.url)
            assert set(second) == {"http://peer-1", "http://peer-2"}
        finally:
            await discovery.close()
            await running.server.stop()

    async def test_discovered_peers_merge_into_a_real_scheduler(self):
        """
        End-to-end peer-exchange: fetch from a live server, then feed
        the result through ``merge_discovered_peers`` into a real
        ``PeerScheduler``, mirroring exactly what
        ``GossipService._discover_from`` does.
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-b")
        running = await _start_server(
            adapter, known_peers=["http://peer-1"], self_address="http://self-b"
        )

        discovery = HTTPPeerDiscovery()
        scheduler = PeerScheduler([running.url])
        try:
            discovered = await discovery.fetch_peers(running.url)
            newly_added = merge_discovered_peers(
                scheduler, discovered, self_address="http://self-a"
            )
            assert set(newly_added) == {"http://self-b", "http://peer-1"}
            assert set(scheduler.peers) == {running.url, "http://self-b", "http://peer-1"}
        finally:
            await discovery.close()
            await running.server.stop()


# ---------------------------------------------------------------------------
# GossipService, end-to-end against a live peer
# ---------------------------------------------------------------------------


class TestGossipServiceOverRealHTTP:
    async def test_gossip_round_converges_a_tracked_session_over_real_http(self):
        runtime_a, runtime_b, session_id = await _paired_replicas_with_shared_base()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)
        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        running_b = await _start_server(adapter_b)
        transport = HTTPGossipTransport()
        service = GossipService(
            adapter_a,
            transport,
            PeerScheduler([running_b.url]),
            secret=SECRET,
            session_ids=[session_id],
        )
        try:
            peer = await service.gossip_round()
            assert peer == running_b.url
            assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
                "root",
                "a",
                "b",
            }
            assert {o.identity.id for o in session_b.knowledge_structure.objects} == {
                "root",
                "a",
                "b",
            }
        finally:
            await transport.close()
            await running_b.server.stop()

    async def test_gossip_round_records_failure_and_backs_off_unreachable_peer(self):
        """
        ``gossip_exchange_over_transport`` only actually calls
        ``transport.exchange`` (and can therefore fail) when this
        replica has *something* local to send -- a tracked session_id
        with no local session is a silent no-op (see its own
        docstring), never reaching the dead peer at all. So this test
        needs a real local session to gossip, or the peer would never
        actually be dialed and ``record_failure`` would never fire.
        """
        runtime_a = await Runtime.create(core=CksCoreAdapter())
        session_a = await runtime_a.create_session(make_structure(["root"]))
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        transport = HTTPGossipTransport()
        dead_port = _free_port()
        dead_peer = f"http://127.0.0.1:{dead_port}"
        scheduler = PeerScheduler([dead_peer])
        service = GossipService(
            adapter_a,
            transport,
            scheduler,
            secret=SECRET,
            session_ids=[session_a.session_id],
        )
        try:
            peer = await service.gossip_round()
            assert peer == dead_peer
            assert scheduler.stats_for(dead_peer).failures == 1
            assert scheduler.eligible_peers() == []  # backed off after one failure
        finally:
            await transport.close()

    async def test_gossip_round_discovers_new_peers_from_a_live_server(self):
        """
        Full loop: A gossips a tracked session with B over real HTTP,
        the round succeeds, and -- because ``discovery`` was supplied
        -- A then asks B (also over real HTTP) which other peers it
        knows about and merges the answer into its own scheduler.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        running_b = await _start_server(
            adapter_b, known_peers=["http://peer-c"], self_address="http://self-b"
        )
        transport = HTTPGossipTransport()
        discovery = HTTPPeerDiscovery()
        scheduler = PeerScheduler([running_b.url])
        service = GossipService(
            adapter_a,
            transport,
            scheduler,
            secret=SECRET,
            session_ids=[session_id],
            discovery=discovery,
            self_address="http://self-a",
        )
        try:
            peer = await service.gossip_round()
            assert peer == running_b.url
            assert set(scheduler.peers) == {running_b.url, "http://self-b", "http://peer-c"}
        finally:
            await transport.close()
            await discovery.close()
            await running_b.server.stop()