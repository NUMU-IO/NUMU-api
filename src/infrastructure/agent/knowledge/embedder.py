"""Embedder behind a swappable interface (Constitution V).

Default model is multilingual-e5-large (1024-dim) via an OpenAI-compatible
/embeddings endpoint. When no endpoint is configured, a deterministic local
fallback is used so the RAG path works offline in dev/test — swapping to the real
model is a config change, not a code change.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Protocol

import httpx

from src.config import settings as app_settings
from src.core.logging import get_logger

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
    """OpenAI-compatible /embeddings client (e.g. a TEI server hosting e5).

    Two knobs exist because the same wire format is served by models with
    different conventions:

    * ``send_dimensions`` asks the server for a specific vector width. Gemini
      returns 3072 by default, and the ``embedding_vec`` column is a fixed
      ``vector(1024)``, so without this every insert would fail on width. A TEI
      server hosting e5 has one fixed width and rejects the field, hence the
      flag rather than always sending it.
    * ``e5_prefixes`` adds the "query: " / "passage: " markers e5 was trained
      with. They are meaningless to Gemini, where they would just be text the
      model has to embed alongside the real content.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        dim: int,
        send_dimensions: bool = False,
        e5_prefixes: bool = True,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._send_dimensions = send_dimensions
        self._e5_prefixes = e5_prefixes

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict = {"model": self._model, "input": inputs}
        if self._send_dimensions:
            payload["dimensions"] = self._dim
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._url}/embeddings",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        vectors = [_l2_normalize(item["embedding"]) for item in data.get("data", [])]
        # Fail loudly here rather than at the INSERT: a width mismatch means the
        # model or the `dimensions` request is wrong, and the pgvector error
        # names neither.
        for v in vectors:
            if len(v) != self._dim:
                raise ValueError(
                    f"Embedder returned {len(v)} dimensions, expected {self._dim}. "
                    f"Check AGENT_EMBED_MODEL / AGENT_EMBED_DIM."
                )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([f"query: {text}" if self._e5_prefixes else text]))[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if self._e5_prefixes:
            texts = [f"passage: {t}" for t in texts]
        return await self._embed(texts)


class HFFeatureExtractionEmbedder:
    """Hugging Face Inference feature-extraction client (free-tier friendly).

    HF's router does not expose an OpenAI-compatible /embeddings route for
    sentence models; it serves raw vectors via
    ``{base}/{model}/pipeline/feature-extraction`` returning a bare array (or a
    list of arrays for batched inputs). Vectors are mean-pooled but not
    normalized, so we L2-normalize to match the rest of the pipeline. Cold starts
    return HTTP 503 ("model is loading") — we retry with a short backoff.
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/{model}/pipeline/feature-extraction"
        self._api_key = api_key
        self._model = model
        self._dim = dim

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"inputs": inputs, "options": {"wait_for_model": True}}
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(4):
                resp = await client.post(self._endpoint, json=payload, headers=headers)
                if resp.status_code == 503:  # model loading — brief cold start
                    logger.info("agent_embedder_hf_loading", attempt=attempt)
                    await asyncio.sleep(5)
                    continue
                resp.raise_for_status()
                break
        data = resp.json()
        # Single input → one vector; batched → list of vectors. Normalize both.
        if data and isinstance(data[0], int | float):
            data = [data]
        return [_l2_normalize(vec) for vec in data]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([f"query: {text}"]))[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([f"passage: {t}" for t in texts])


def get_embedder() -> Embedder:
    s = app_settings
    if s.agent_embed_url:
        if s.agent_embed_provider == "hf":
            return HFFeatureExtractionEmbedder(
                base_url=s.agent_embed_url,
                api_key=s.agent_embed_api_key,
                model=s.agent_embed_model,
                dim=s.agent_embed_dim,
            )
        if s.agent_embed_provider == "google":
            # Gemini over its OpenAI-compatible endpoint. Reuses the platform's
            # existing Google key, so the agent needs no second credential.
            return HttpEmbedder(
                url=s.agent_embed_url,
                api_key=s.agent_embed_api_key,
                model=s.agent_embed_model,
                dim=s.agent_embed_dim,
                send_dimensions=True,
                e5_prefixes=False,
            )
        return HttpEmbedder(
            url=s.agent_embed_url,
            api_key=s.agent_embed_api_key,
            model=s.agent_embed_model,
            dim=s.agent_embed_dim,
        )
    logger.info("agent_embedder_fallback", reason="AGENT_EMBED_URL unset")
    return FallbackEmbedder(dim=s.agent_embed_dim)


def embedder_signature() -> str:
    """Short identity of the active embedder — folded into the doc content hash so
    that switching models/providers (or moving off the hash fallback) re-embeds on
    the next ingest instead of being skipped as unchanged."""
    s = app_settings
    if s.agent_embed_url:
        return f"{s.agent_embed_provider}:{s.agent_embed_model}:{s.agent_embed_dim}"
    return f"fallback:{s.agent_embed_dim}"
