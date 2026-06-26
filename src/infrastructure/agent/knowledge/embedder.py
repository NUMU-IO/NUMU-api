"""Embedder behind a swappable interface (Constitution V).

Default model is multilingual-e5-large (1024-dim) via an OpenAI-compatible
/embeddings endpoint. When no endpoint is configured, a deterministic local
fallback is used so the RAG path works offline in dev/test — swapping to the real
model is a config change, not a code change.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from src.config import settings as app_settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class Embedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


class FallbackEmbedder:
    """Deterministic hashing embedder — offline/dev only (not semantic-quality).

    Hashes tokens into `dim` buckets and L2-normalizes. Same text → same vector,
    overlapping text → higher cosine, which is enough to exercise retrieval.
    """

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in (text or "").lower().split():
            # Not security-sensitive: MD5 only buckets tokens for the offline fallback.
            h = int(
                hashlib.md5(token.encode("utf-8"), usedforsecurity=False).hexdigest(),
                16,
            )
            vec[h % self._dim] += 1.0
        return _l2_normalize(vec)

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]


class HttpEmbedder:
    """OpenAI-compatible /embeddings client (e.g. a TEI server hosting e5)."""

    def __init__(self, *, url: str, api_key: str, model: str, dim: int) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dim

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._url}/embeddings",
                json={"model": self._model, "input": inputs},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return [_l2_normalize(item["embedding"]) for item in data.get("data", [])]

    async def embed_query(self, text: str) -> list[float]:
        # e5 convention: queries are prefixed "query: ".
        return (await self._embed([f"query: {text}"]))[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([f"passage: {t}" for t in texts])


def get_embedder() -> Embedder:
    s = app_settings
    if s.agent_embed_url:
        return HttpEmbedder(
            url=s.agent_embed_url,
            api_key=s.agent_embed_api_key,
            model=s.agent_embed_model,
            dim=s.agent_embed_dim,
        )
    logger.info("agent_embedder_fallback", reason="AGENT_EMBED_URL unset")
    return FallbackEmbedder(dim=s.agent_embed_dim)
