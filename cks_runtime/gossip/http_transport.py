"""
``HTTPGossipTransport`` / ``GossipServer`` -- the reference
``GossipTransport`` implementation ADR-008 calls for ("the reference
implementation (HTTP long-poll) is intentionally minimal, not a
recommendation of gRPC/libp2p/etc").

``GossipServer``'s lifecycle (build an aiohttp ``Application``, start
it via ``AppRunner``/``TCPSite``, tear both down in ``stop``) follows
the same shape as another project's gossip server lifecycle
management reviewed for reuse -- that part carries over because
starting and stopping an aiohttp server is inherently generic, not
specific to what the server does once running. Everything the server
actually *does* (the ``/gossip/{session_id}`` route, request/response
shape, what "receiving a gossip message" means) is new: that project
gossiped opaque dict payloads into a CRDT genome store, this one
exchanges signed ``RuntimeSession`` snapshots through
``GossipAdapter.apply_remote_session``.

Also hosts ``HTTPPeerDiscovery`` and ``GossipServer``'s
``/gossip/peers`` route -- the HTTP side of the peer-exchange protocol
``discovery.py`` defines. Kept in this module rather than a dedicated
``http_discovery.py`` because it shares ``GossipServer`` (one aiohttp
``Application``, one running server per replica) and
``HTTPGossipTransport``'s connection-pooling ``ClientSession`` pattern
almost verbatim; splitting it out would only duplicate both.

Requires the ``aiohttp`` extra (``pip install cks-runtime[gossip]``);
importing this module without it raises ``ImportError`` with that
hint, matching how ``postgres_storage.py`` is only ever imported
lazily, on demand, so the base install stays dependency-light.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

try:
    import aiohttp
    from aiohttp import web
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "HTTPGossipTransport/GossipServer require the 'gossip' extra: "
        "pip install cks-runtime[gossip]"
    ) from exc

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.discovery import PeerDiscovery, PeerDiscoveryError
from cks_runtime.gossip.envelope import GossipEnvelope
from cks_runtime.gossip.filter import GossipFilter
from cks_runtime.gossip.transport import GossipTransport, GossipTransportError

logger = logging.getLogger(__name__)

#: Default per-request timeout for the client side. Generous relative
#: to a typical LAN round trip because a peer's reply carries a full
#: KnowledgeStructure snapshot, not a small control message.
DEFAULT_REQUEST_TIMEOUT_S = 10.0


class HTTPGossipTransport(GossipTransport):
    """
    Client side: ``POST {peer}/gossip/{session_id}`` with the local
    envelope as the JSON body, expecting the peer's own envelope (or
    an empty 404) back.

    One instance can be reused across many ``exchange`` calls to many
    peers -- it lazily opens a single ``aiohttp.ClientSession`` and
    keeps it for connection pooling, matching
    ``aiohttp.ClientSession``'s own recommended usage (one session per
    long-lived component, not one per request). Call ``close()`` when
    done with it (e.g. alongside ``GossipService.stop()``).
    """

    def __init__(self, *, request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._request_timeout_s = request_timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def _client_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
        session = await self._client_session()
        url = f"{peer.rstrip('/')}/gossip/{envelope.session_id}"
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)

        try:
            async with session.post(url, json=envelope.to_dict(), timeout=timeout) as response:
                if response.status == 404:
                    return None
                if response.status != 200:
                    body = await response.text()
                    raise GossipTransportError(
                        f"peer {peer!r} returned HTTP {response.status}: {body[:200]!r}"
                    )
                data: Any = await response.json()
        except aiohttp.ClientError as exc:
            raise GossipTransportError(f"failed to reach peer {peer!r}: {exc}") from exc
        except TimeoutError as exc:
            raise GossipTransportError(f"timed out reaching peer {peer!r}") from exc

        try:
            return GossipEnvelope.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise GossipTransportError(f"malformed reply from peer {peer!r}: {exc}") from exc

    async def close(self) -> None:
        """Release the pooled ``ClientSession``, if one was opened."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class HTTPPeerDiscovery(PeerDiscovery):
    """
    Client side of the peer-exchange protocol (``discovery.py``):
    ``GET {peer}/gossip/peers``, expecting ``{"peers": [...]}`` back.

    Mirrors ``HTTPGossipTransport`` exactly (lazily opened, pooled
    ``aiohttp.ClientSession``; same error-wrapping shape) -- kept as a
    separate class rather than a second method on
    ``HTTPGossipTransport`` because the two exchanges are independent
    protocols (see this module's docstring) that a caller may want to
    use, retry, or close on different schedules.
    """

    def __init__(self, *, request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._request_timeout_s = request_timeout_s
        self._session: aiohttp.ClientSession | None = None

    async def _client_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fetch_peers(self, peer: str) -> list[str]:
        session = await self._client_session()
        url = f"{peer.rstrip('/')}/gossip/peers"
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)

        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    body = await response.text()
                    raise PeerDiscoveryError(
                        f"peer {peer!r} returned HTTP {response.status}: {body[:200]!r}"
                    )
                data: Any = await response.json()
        except aiohttp.ClientError as exc:
            raise PeerDiscoveryError(f"failed to reach peer {peer!r}: {exc}") from exc
        except TimeoutError as exc:
            raise PeerDiscoveryError(f"timed out reaching peer {peer!r}") from exc

        peers = data.get("peers") if isinstance(data, dict) else None
        if not isinstance(peers, list):
            raise PeerDiscoveryError(f"malformed peer list from peer {peer!r}: {data!r}")
        return [str(p) for p in peers]

    async def close(self) -> None:
        """Release the pooled ``ClientSession``, if one was opened."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class GossipServer:
    """
    Server side: receives a peer's envelope, applies it locally
    through ``GossipAdapter.apply_remote_session``, and replies with
    this replica's own (now possibly converged) snapshot of the same
    session -- one HTTP round trip does what
    ``exchange.gossip_exchange`` does with two in-process calls.

    Owns its own aiohttp ``Application``/``AppRunner``/``TCPSite`` --
    unlike the adapter this is layered on (``GossipAdapter`` wraps a
    ``Runtime`` and has no transport opinion at all), this class *is*
    the transport-facing half of gossip for one replica, matching
    ADR-006's Adapter pattern: mechanism lives here, merge policy
    stays in ``GossipAdapter``.

    Also serves ``GET /gossip/peers`` -- the server side of the
    peer-exchange protocol ``discovery.py`` defines -- when
    ``known_peers`` is supplied: a callable (evaluated fresh on every
    request, so it can return a live ``PeerScheduler.peers`` snapshot)
    or a plain, fixed iterable. Omitted (the default), the route still
    exists but always reports an empty peer list; it is never an
    error for a caller not to have wired peer discovery up.
    ``self_address``, when given, is always included in that list --
    this is what lets a replica be *discovered* by others even if it
    was never listed in anyone's own static configuration, matching
    the reachable-through-a-seed model peer-exchange protocols rely
    on.
    """

    def __init__(
        self,
        adapter: GossipAdapter,
        *,
        secret: bytes,
        host: str = "0.0.0.0",
        port: int,
        gossip_filter: GossipFilter | None = None,
        known_peers: Callable[[], Iterable[str]] | Iterable[str] | None = None,
        self_address: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._secret = secret
        self._host = host
        self._port = port
        self._filter = gossip_filter if gossip_filter is not None else GossipFilter()
        self._known_peers = known_peers
        self._self_address = self_address

        self._reply_seq_no = 0
        self._running = False
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def __repr__(self) -> str:
        return (
            f"GossipServer(replica_id={self._adapter.replica_id!r}, "
            f"host={self._host!r}, port={self._port}, running={self._running})"
        )

    @property
    def running(self) -> bool:
        return self._running

    def _next_reply_seq_no(self) -> int:
        self._reply_seq_no += 1
        return self._reply_seq_no

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/gossip/{session_id}", self._handle_gossip)
        app.router.add_get("/gossip/peers", self._handle_peers)
        app.router.add_get("/health", self._handle_health)
        return app

    async def start(self) -> None:
        """Start the gossip HTTP server. No-op if already running."""
        if self._running:
            logger.debug("GossipServer already running.")
            return

        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        self._running = True
        logger.info(
            "GossipServer started on %s:%s replica_id=%s",
            self._host,
            self._port,
            self._adapter.replica_id,
        )

    async def stop(self) -> None:
        """Stop the gossip HTTP server. No-op if not running."""
        if not self._running and self._runner is None:
            return

        self._running = False

        if self._site is not None:
            await self._site.stop()
            self._site = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        logger.info("GossipServer stopped.")

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok" if self._running else "stopped",
                "replica_id": self._adapter.replica_id,
            }
        )

    async def _handle_peers(self, request: web.Request) -> web.Response:
        """
        Serve this replica's known peer addresses (peer-exchange,
        ``discovery.py``). Deliberately unauthenticated and unsigned,
        unlike ``/gossip/{session_id}`` -- a peer *address* is not
        sensitive the way a session snapshot is, and requiring a
        signed request here would mean a brand-new replica (which by
        definition hasn't exchanged a session yet) could never
        discover anyone. Matches ADR-008's Non-Goals ("Not solving
        Byzantine or malicious peers... assumes cooperating agents
        within one deployment") exactly as the rest of this package
        already does.
        """
        if self._known_peers is None:
            peers: Iterable[str] = ()
        elif callable(self._known_peers):
            peers = self._known_peers()
        else:
            peers = self._known_peers

        peer_list = list(dict.fromkeys(peers))  # de-duplicate, preserve order
        if self._self_address is not None and self._self_address not in peer_list:
            peer_list.insert(0, self._self_address)

        return web.json_response({"peers": peer_list})

    async def _handle_gossip(self, request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]

        try:
            data: Any = await request.json()
        except (aiohttp.ContentTypeError, ValueError) as exc:
            return web.json_response({"error": f"malformed request body: {exc}"}, status=400)

        try:
            remote_envelope = GossipEnvelope.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            return web.json_response({"error": f"malformed envelope: {exc}"}, status=400)

        if remote_envelope.session_id != session_id:
            return web.json_response(
                {"error": "session_id in URL does not match envelope"}, status=400
            )

        if not remote_envelope.verify(self._secret):
            logger.warning(
                "GossipServer: signature verification failed, claimed sender=%s",
                remote_envelope.sender_replica_id,
            )
            return web.json_response({"error": "signature verification failed"}, status=401)

        if not self._filter.check(
            remote_envelope.sender_replica_id,
            remote_envelope.nonce,
            remote_envelope.seq_no,
            remote_envelope.timestamp_ms,
        ):
            logger.warning(
                "GossipServer: envelope from sender=%s rejected by replay filter",
                remote_envelope.sender_replica_id,
            )
            return web.json_response({"error": "rejected by replay filter"}, status=409)

        # Apply through the ordinary merge path. A conflict is
        # escalated by apply_remote_session itself (GossipConflictDetected
        # via the EventBus) rather than surfaced as an HTTP error --
        # the sender is not the right place to resolve it (see
        # ADR-008's "Conflict escalation via EventBus" section).
        await self._adapter.apply_remote_session(remote_envelope.to_session())

        local_session = self._adapter._runtime.get_session(session_id)
        if local_session is None:
            # This replica doesn't track session_id either -- nothing
            # to reply with. Matches GossipTransport.exchange's
            # documented "peer reachable but doesn't track this
            # session" outcome, not an error.
            return web.json_response(None, status=404)

        reply_envelope = GossipEnvelope.from_session(
            local_session,
            sender_replica_id=self._adapter.replica_id,
            seq_no=self._next_reply_seq_no(),
            secret=self._secret,
        )
        return web.json_response(reply_envelope.to_dict())