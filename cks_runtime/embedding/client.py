"""
EmbeddingClient — abstract interface for embedding providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmbeddingClient(ABC):
    """Abstract embedding client."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """
        Generate embeddings for a list of texts.

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

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        import hashlib
        import struct
        embeddings = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            embedding = bytes()
            for i in range(0, len(digest), 4):
                val = struct.unpack("f", digest[i:i+4])[0]
                embedding += struct.pack("f", val)
            while len(embedding) < self._dimension * 4:
                embedding += struct.pack("f", 0.0)
            embeddings.append(embedding)
        return embeddings


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

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        import os
        import openai
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=self._model,
            input=texts,
        )
        embeddings = []
        for item in response.data:
            import struct
            emb = bytes()
            for val in item.embedding:
                emb += struct.pack("f", val)
            embeddings.append(emb)
        return embeddings


class HuggingFaceEmbeddingClient(EmbeddingClient):
    """Free Hugging Face Inference API client."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        import os
        self._model_name = model_name
        self._token = os.environ.get("HF_TOKEN")
        if not self._token:
            raise ValueError("HF_TOKEN environment variable is not set")
        self._dimension = 384

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        import requests
        import struct

        api_url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{self._model_name}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.post(api_url, headers=headers, json={"inputs": texts, "options": {"wait_for_model": True}})
        response.raise_for_status()
        outputs = response.json()

        if isinstance(texts, str):
            outputs = [outputs]

        result = []
        for emb in outputs:
            emb_bytes = bytes()
            for val in emb:
                emb_bytes += struct.pack("f", float(val))
            result.append(emb_bytes)
        return result