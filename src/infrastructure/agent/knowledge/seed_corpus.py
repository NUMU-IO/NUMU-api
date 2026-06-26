"""Starter NUMU knowledge corpus (Layer A).

A small curated seed so the Agent can "introduce NUMU" and answer common how-to
questions on day one. Comprehensive authoring across every NUMU feature + growth
playbooks is a sizeable ongoing effort tracked as its own spec (002 — NUMU
Knowledge Base). Ingestion in production runs via n8n calling the upsert endpoint.
"""

from __future__ import annotations

from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository

# Each doc: source, title, section, locale, chunks (each chunk is embedded separately).
CORPUS: list[dict] = [
    {
        "source": "numu-docs/payments/paymob",
        "title": "Set up Paymob payments",
        "section": "Payments",
        "locale": "en",
        "chunks": [
            "To accept card payments with Paymob on NUMU, go to Settings → Payments, choose "
            "Paymob, and enter your Paymob API key and integration ID. Save, then toggle Paymob "
            "on. Test with a small order before going live.",
        ],
    },
    {
        "source": "numu-docs/payments/cod",
        "title": "Enable Cash on Delivery (COD)",
        "section": "Payments",
        "locale": "en",
        "chunks": [
            "Cash on Delivery lets customers pay when the order arrives. Enable it under "
            "Settings → Payments → Cash on Delivery. You can require a partial deposit and set "
            "a COD fee per governorate.",
        ],
    },
    {
        "source": "numu-docs/marketing/bogo",
        "title": "What is a BOGO campaign",
        "section": "Marketing",
        "locale": "en",
        "chunks": [
            "BOGO (Buy One, Get One) is a promotion where buying a qualifying product unlocks "
            "another for free or at a discount. In NUMU, create one under Marketing → Promotions "
            "→ New → BOGO, pick the trigger products and the reward, and set the date range.",
        ],
    },
    {
        "source": "numu-docs/marketing/abandoned-cart",
        "title": "Recover abandoned carts",
        "section": "Marketing",
        "locale": "en",
        "chunks": [
            "NUMU detects carts a shopper left without checking out and can send automated "
            "recovery messages by email or WhatsApp. Enable it under Marketing → Abandoned "
            "Checkouts and customize the message and timing.",
        ],
    },
    {
        "source": "numu-docs/theme/editor-v3",
        "title": "How the theme editor works",
        "section": "Online Store",
        "locale": "en",
        "chunks": [
            "The NUMU theme editor (V3) lets you customize your storefront with sections and "
            "settings. Open Online Store → Themes → Customize. Add a section, edit its settings, "
            "preview live, and Publish. Every change is versioned, so you can restore a previous "
            "version at any time.",
        ],
    },
    {
        "source": "numu-docs/shipping/bosta",
        "title": "Connect Bosta shipping",
        "section": "Shipping",
        "locale": "en",
        "chunks": [
            "Bosta is an Egyptian courier you can connect for automated shipping labels and "
            "tracking. Go to Settings → Shipping → Bosta, paste your Bosta API key, and select "
            "the pickup location. Orders can then create Bosta shipments automatically.",
        ],
    },
]


async def seed_shared_corpus(session) -> int:
    """Embed + upsert the starter corpus into Layer A. Returns chunk count."""
    embedder = get_embedder()
    repo = KnowledgeRepository(session)
    total = 0
    for doc in CORPUS:
        embeddings = await embedder.embed_passages(doc["chunks"])
        await repo.upsert_shared_doc(
            source=doc["source"],
            title=doc["title"],
            section=doc.get("section"),
            locale=doc.get("locale", "en"),
            chunks=doc["chunks"],
            embeddings=embeddings,
        )
        total += len(doc["chunks"])
    return total
