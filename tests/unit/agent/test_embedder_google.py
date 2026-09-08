"""The Google embeddings path: right width, no e5 prefixes, loud on mismatch.

Gemini returns 3072 dimensions by default and `embedding_vec` is a fixed
`vector(1024)`, so the width is not a detail — every insert depends on it.
"""

from __future__ import annotations

import httpx
import pytest

from src.infrastructure.agent.knowledge import embedder as mod
from src.infrastructure.agent.knowledge.embedder import HttpEmbedder


class _Capture:
    """Stands in for httpx.AsyncClient and records the request body."""

    def __init__(self, width: int):
        self.width = width
        self.sent: dict | None = None

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.sent = json
        vectors = [{"embedding": [0.1] * self.width} for _ in range(len(json["input"]))]
        # A Response needs its request set before raise_for_status() will work.
        return httpx.Response(
            200, json={"data": vectors}, request=httpx.Request("POST", url)
        )


@pytest.mark.asyncio
async def test_google_mode_asks_for_the_column_width(monkeypatch):
    cap = _Capture(width=1024)
    monkeypatch.setattr(mod.httpx, "AsyncClient", cap)
    emb = HttpEmbedder(
        url="https://x/v1beta/openai",
        api_key="k",
        model="gemini-embedding-001",
        dim=1024,
        send_dimensions=True,
        e5_prefixes=False,
    )
    out = await emb.embed_passages(["how do I connect Bosta"])

    assert cap.sent["dimensions"] == 1024
    # No "passage: " marker — that is an e5 convention and Gemini would just
    # embed the literal word.
    assert cap.sent["input"] == ["how do I connect Bosta"]
    assert len(out[0]) == 1024


@pytest.mark.asyncio
async def test_default_mode_is_unchanged(monkeypatch):
    """A TEI server hosting e5 keeps the prefixes and gets no dimensions field."""
    cap = _Capture(width=1024)
    monkeypatch.setattr(mod.httpx, "AsyncClient", cap)
    emb = HttpEmbedder(url="https://x", api_key="k", model="e5", dim=1024)
    await emb.embed_query("orders today")

    assert "dimensions" not in cap.sent
    assert cap.sent["input"] == ["query: orders today"]


@pytest.mark.asyncio
async def test_a_wrong_width_fails_here_not_at_the_insert(monkeypatch):
    """pgvector's error names neither the model nor the setting; this one does."""
    cap = _Capture(width=3072)  # what Gemini returns when dimensions is ignored
    monkeypatch.setattr(mod.httpx, "AsyncClient", cap)
    emb = HttpEmbedder(
        url="https://x",
        api_key="k",
        model="gemini-embedding-001",
        dim=1024,
        send_dimensions=True,
        e5_prefixes=False,
    )
    with pytest.raises(ValueError) as err:
        await emb.embed_passages(["x"])
    assert "3072" in str(err.value) and "AGENT_EMBED_MODEL" in str(err.value)
