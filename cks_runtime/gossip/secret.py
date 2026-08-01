"""
HMAC signing secret for ``GossipEnvelope`` (ADR-008).

Deliberately a **separate secret** from cks-mcp's provenance signing
(``CKS_MCP_SECRET``, that repo's ADR-002 / ``cks_mcp.provenance``).
The two prove different claims -- "this fact was checked against this
URL" versus "this message really came from replica X" -- and cks-mcp
depends on cks-runtime, not the other way around, so cks-runtime
cannot import cks-mcp's secret even if it wanted to. Sharing one
secret across both trust domains would also mean a compromised gossip
peer could forge provenance records, or vice versa. See
``envelope.py``'s module docstring for the full rationale.

Same loading strategy as ``cks_mcp.provenance._load_secret``:
environment variable first, then a persisted per-installation file,
generating one on first use. Deliberately *not* a module-level
singleton (unlike ``cks_mcp.provenance``) -- callers (``GossipService``,
``HTTPGossipTransport``, tests) hold the secret explicitly instead of
relying on import-time file I/O, which makes the loading path easier
to test and to override per-Runtime-instance.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

#: Overrides the loaded secret outright. Accepts raw text, hex
#: (``bytes.fromhex``), or a ``base64:``-prefixed value -- same three
#: forms ``cks_mcp.provenance`` accepts, for operator familiarity.
ENV_VAR = "CKS_GOSSIP_SECRET"

#: Overrides where the persisted secret file lives. Defaults to a
#: per-user directory so multiple replicas on one machine (e.g. local
#: multi-process tests) don't collide with a system-wide path.
DATA_DIR_ENV_VAR = "CKS_RUNTIME_DATA_DIR"

_SECRET_FILENAME = "gossip_secret"


def default_secret_path() -> Path:
    """
    Where the persisted gossip secret lives absent ``ENV_VAR``.

    ``DATA_DIR_ENV_VAR`` overrides the parent directory; otherwise
    ``~/.cks_runtime``.
    """
    override = os.environ.get(DATA_DIR_ENV_VAR)
    base = Path(override) if override else Path.home() / ".cks_runtime"
    return base / _SECRET_FILENAME


def load_secret(path: Path | None = None) -> bytes:
    """
    Return a stable HMAC signing secret for gossip envelopes.

    Resolution order, identical in spirit to
    ``cks_mcp.provenance._load_secret``:

    1. ``CKS_GOSSIP_SECRET`` environment variable (raw, hex, or
       ``base64:``-prefixed).
    2. A previously persisted secret file at ``path`` (default
       ``default_secret_path()``).
    3. Generate 32 random bytes and persist them for next time.

    Never raises on a non-writable filesystem -- an unpersisted
    freshly generated secret is still usable for the current process,
    it just won't survive a restart (the caller finds out the hard
    way, via failed signature verification against peers that did
    persist theirs -- this mirrors ``cks_mcp.provenance`` exactly).
    """
    raw = os.environ.get(ENV_VAR)
    if raw:
        if raw.startswith("base64:"):
            return base64.b64decode(raw.removeprefix("base64:"))
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return raw.encode("utf-8")

    secret_path = path if path is not None else default_secret_path()

    try:
        return secret_path.read_bytes()
    except (FileNotFoundError, OSError):
        pass

    secret = os.urandom(32)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_bytes(secret)
    except OSError:
        pass
    return secret