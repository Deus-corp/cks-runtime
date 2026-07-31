"""
EmbeddingClient — abstract interface for embedding providers.
"""

from __future__ import annotations

import math
import struct
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

# Retry tuning for transient Hugging Face Inference API failures
# (timeouts, connection errors, rate limiting, server errors). Mirrors
# the busy-wait/backoff pattern used for SQLite lock contention
# elsewhere (see cks_runtime.storage.sqlite_storage._retry_on_locked)
# rather than inventing a second one here.
_HF_REQUEST_TIMEOUT_SECONDS = 30
_HF_MAX_RETRIES = 3
_HF_RETRY_BASE_DELAY_SECONDS = 1.0


def _is_retryable_hf_error(exc: Exception) -> bool:
    """
    Whether exc is a transient Hugging Face API failure worth retrying:
    a network-level timeout/connection error, or an HTTP 429 (rate
    limit) / 5xx (server-side) response. Any other HTTP error (bad
    model name, malformed payload, invalid token) is the caller's
    fault and won't succeed on retry, so it's raised immediately.
    """
    import requests

    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status == 429 or (status is not None and 500 <= status < 600)
    return False


def _retry_on_transient_hf_error[T](fn: Callable[[], T]) -> T:
    """Run fn(), retrying with exponential backoff on transient Hugging Face API failures."""
    last_exc: BaseException | None = None
    for attempt in range(_HF_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:
            if not _is_retryable_hf_error(exc) or attempt >= _HF_MAX_RETRIES - 1:
                raise
            last_exc = exc
            time.sleep(_HF_RETRY_BASE_DELAY_SECONDS * (2**attempt))
    assert last_exc is not None
    raise last_exc


def _normalize_vector(emb: bytes) -> bytes:
    """Normalize a byte-encoded float vector to unit length."""
    n = len(emb) // 4
    vals = struct.unpack(f"{n}f", emb)
    norm = math.sqrt(sum(v * v for v in vals))
    if norm == 0.0:
        norm = 1.0
    return struct.pack(f"{n}f", *(v / norm for v in vals))


class EmbeddingClient(ABC):
    """Abstract embedding client."""

    @abstractmethod
    def embed_batch(self, texts: list[str], *, normalize: bool = False) -> list[bytes]:
        """
        Generate embeddings for a list of texts.

        If normalize is True, the returned vectors will have unit length.
        Returns a list of byte strings representing the embedding vectors.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimension of the embedding vectors."""
        ...


class StubEmbeddingClient(EmbeddingClient):
    """
    Stub embedding client — uses SHA-256 hashing for deterministic,
    non-semantic embeddings. For testing only.
    """

    def __init__(self) -> None:
        self._dimension = 384

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_batch(self, texts: list[str], *, normalize: bool = False) -> list[bytes]:
        import hashlib
        embeddings = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            embedding = b""
            for i in range(0, len(digest), 4):
                val = struct.unpack("f", digest[i:i+4])[0]
                embedding += struct.pack("f", val)
            while len(embedding) < self._dimension * 4:
                embedding += struct.pack("f", 0.0)
            embeddings.append(embedding)
        if normalize:
            embeddings = [_normalize_vector(e) for e in embeddings]
        return embeddings


class FastEmbedEmbeddingClient(EmbeddingClient):
    """
    Local embedding client backed by fastembed (ONNX Runtime, CPU-only).

    Unlike HuggingFaceEmbeddingClient, this needs no API token and makes
    no per-query network calls: the model is downloaded once on first
    use and cached on disk.
    """
    _DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None) -> None:
        import os
        self._model_name = model_name or os.environ.get("CKS_EMBEDDING_MODEL") or self._DEFAULT_MODEL
        self._cache_dir = cache_dir or os.environ.get("CKS_EMBEDDING_CACHE_DIR")
        self._model: Any = None
        self._dimension: int | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "fastembed is not installed. Install it with "
                    "`pip install fastembed` (or `pip install cks-runtime[fastembed]`) "
                    "to use local, token-free embeddings."
                ) from exc
            self._model = TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir)
        return self._model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            probe = self.embed_batch(["probe"], normalize=False)[0]
            self._dimension = len(probe) // 4
        return self._dimension

    def embed_batch(self, texts: list[str], *, normalize: bool = False) -> list[bytes]:
        model = self._ensure_model()
        result = []
        for vec in model.embed(texts):
            result.append(struct.pack(f"{len(vec)}f", *(float(v) for v in vec)))
        if normalize:
            result = [_normalize_vector(e) for e in result]
        return result


class OpenAIEmbeddingClient(EmbeddingClient):
    """
    OpenAI embedding client. Requires OPENAI_API_KEY env var.
    """

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self._model = model
        self._dimension = 1536  # default for text-embedding-3-small

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_batch(self, texts: list[str], *, normalize: bool = False) -> list[bytes]:
        import os

        import openai
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=self._model,
            input=texts,
        )
        embeddings = []
        for item in response.data:
            emb = b""
            for val in item.embedding:
                emb += struct.pack("f", val)
            embeddings.append(emb)
        if normalize:
            embeddings = [_normalize_vector(e) for e in embeddings]
        return embeddings


class HuggingFaceEmbeddingClient(EmbeddingClient):
    """Free Hugging Face Inference API client.

    Model can be overridden via the CKS_EMBEDDING_MODEL env var.
    Dimension is detected from the first API response (lazy), or can
    be set explicitly via CKS_EMBEDDING_DIMENSION.
    """

    def __init__(self, model_name: str | None = None) -> None:
        import os
        self._model_name = (
            model_name
            or os.environ.get("CKS_EMBEDDING_MODEL")
            or "sentence-transformers/all-MiniLM-L6-v2"
        )
        self._token = os.environ.get("HF_TOKEN")
        if not self._token:
            raise ValueError("HF_TOKEN environment variable is not set")
        # Dimension is lazy-detected from the first embedding response,
        # unless explicitly set via env var.
        explicit_dim = os.environ.get("CKS_EMBEDDING_DIMENSION")
        self._dimension: int | None
        if explicit_dim is not None:
            self._dimension = int(explicit_dim)
        else:
            self._dimension = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # Probe with a single word to detect dimension
            probe = self.embed_batch(["probe"], normalize=False)[0]
            self._dimension = len(probe) // 4  # 4 bytes per float
        return self._dimension

    def embed_batch(self, texts: list[str], *, normalize: bool = False, is_query: bool = False) -> list[bytes]:
        import requests

        api_url = f"https://router.huggingface.co/hf-inference/models/{self._model_name}/pipeline/feature-extraction"
        headers = {"Authorization": f"Bearer {self._token}"}

        def _do_request() -> Any:
            response = requests.post(
                api_url,
                headers=headers,
                json={"inputs": texts, "options": {"wait_for_model": True}},
                timeout=_HF_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()

        outputs = _retry_on_transient_hf_error(_do_request)

        if isinstance(outputs, list) and len(outputs) > 0 and isinstance(outputs[0], float):
            outputs = [outputs]

        result = []
        for emb in outputs:
            emb_bytes = b""
            for val in emb:
                emb_bytes += struct.pack("f", float(val))
            result.append(emb_bytes)
        if normalize:
            result = [_normalize_vector(e) for e in result]
        return result