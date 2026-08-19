"""
safe_fetch: SSRF/DNS-rebinding-safe outbound HTTP GET, for Runtime
subsystems that need to make real network requests themselves.

KEEP IN SYNC WITH cks-mcp: this is a deliberate, trimmed port of
`cks_mcp.tools.verify_source.handler._safe_request` (GET-only -- this
module has no need for HEAD/other methods). Runtime must not import
from cks-mcp (cks-mcp depends on cks-runtime, never the other way --
see ADR-001, Runtime Layering), so the SSRF protections are duplicated
here rather than shared via import. Any change to the safety logic in
`verify_source._safe_request` (hostname validation, private-IP
ranges, DNS pinning, redirect handling) should be mirrored here.

Used by `cks_runtime.reasoning.graph_auto_update_sweeper` to fetch
`_version.py` files from raw.githubusercontent.com without opening an
SSRF hole into the deployment's internal network.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import requests

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 5

# Same rationale as verify_source._USER_AGENT: some hosts (notably
# Wikimedia) 403 requests carrying requests' generic default UA.
_USER_AGENT = "cks-runtime/1.0 (+https://github.com/Deus-corp/cks-runtime)"


class UnsafeURLError(ValueError):
    """Raised when a URL is not a safe target for an outbound request."""


# Not classified as private/reserved/etc by `ipaddress`, but not
# globally-routable public internet either -- see verify_source's
# identical comment for why these need an explicit block (RFC 6598
# Shared Address Space / CGNAT, RFC 6890 IETF Protocol Assignments).
_EXTRA_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "100.64.0.0/10",
        "192.0.0.0/24",
    )
)


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if any(ip in network for network in _EXTRA_BLOCKED_NETWORKS):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# ---------------------------------------------------------------------------
# DNS rebinding protection via thread-local getaddrinfo override.
# Mirrors verify_source.pin_dns exactly (including the reference-
# counted activation guard, needed so two concurrent pin_dns() contexts
# on different threads don't stomp on each other's restore).
# ---------------------------------------------------------------------------

_orig_getaddrinfo = socket.getaddrinfo
_thread_local = threading.local()
_patch_lock = threading.Lock()
_active_patches = 0


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    overrides = getattr(_thread_local, "dns_overrides", {})
    if host in overrides:
        host = overrides[host]
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


@contextmanager
def pin_dns(hostname: str, ip: str):
    """Temporarily pin a hostname to a specific IP for the duration of the context."""
    global _active_patches
    if not hasattr(_thread_local, "dns_overrides"):
        _thread_local.dns_overrides = {}
    old_ip = _thread_local.dns_overrides.get(hostname)
    _thread_local.dns_overrides[hostname] = ip
    with _patch_lock:
        _active_patches += 1
        if _active_patches == 1:
            socket.getaddrinfo = _patched_getaddrinfo
    try:
        yield
    finally:
        with _patch_lock:
            _active_patches -= 1
            if _active_patches == 0:
                socket.getaddrinfo = _orig_getaddrinfo
        if old_ip is None:
            del _thread_local.dns_overrides[hostname]
        else:
            _thread_local.dns_overrides[hostname] = old_ip


def _resolve_and_validate_host(url: str) -> tuple[str, list[str]]:
    """Resolve and validate `url`'s hostname; see verify_source's twin
    for the full rationale (IPv4-first ordering, all-candidates-public
    requirement)."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only http/https are allowed."
        )
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL has no hostname.")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host '{hostname}': {exc}") from exc

    seen: set[str] = set()
    ipv4: list[str] = []
    ipv6: list[str] = []
    for family, _, _, _, sockaddr in addrinfo:
        ip = str(sockaddr[0])
        if ip in seen:
            continue
        seen.add(ip)
        (ipv4 if family == socket.AF_INET else ipv6).append(ip)
    resolved_ips = ipv4 + ipv6

    if not resolved_ips or not all(_is_public_ip(ip) for ip in resolved_ips):
        raise UnsafeURLError(
            f"URL host '{hostname}' resolves to a non-public address "
            f"({', '.join(sorted(seen))}); refusing to fetch."
        )

    return hostname, resolved_ips


def safe_get(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> requests.Response | None:
    """
    Perform a GET request against `url`, safe against SSRF and DNS
    rebinding -- see `verify_source._safe_request` for the full
    explanation of the resolve-validate-then-pin approach and manual
    (re-validated) redirect handling this mirrors.

    Returns the final `Response`, or `None` if every validated
    candidate IP failed to connect, or a redirect target failed
    validation. Raises `UnsafeURLError` if `url` itself is unsafe.
    Performs blocking network I/O -- callers should dispatch via
    `asyncio.to_thread` rather than calling this directly from an
    async context.
    """
    hostname, candidate_ips = _resolve_and_validate_host(url)
    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT

    for _ in range(_MAX_REDIRECTS + 1):
        resp = None
        for ip in candidate_ips:
            with pin_dns(hostname, ip):
                try:
                    resp = session.get(url, timeout=timeout, allow_redirects=False)
                    break
                except requests.RequestException:
                    continue

        if resp is None:
            return None

        if resp.is_redirect and resp.headers.get("Location"):
            new_url = urljoin(url, resp.headers["Location"])
            try:
                hostname, candidate_ips = _resolve_and_validate_host(new_url)
                url = new_url
            except UnsafeURLError:
                return None
            continue
        return resp

    return None
