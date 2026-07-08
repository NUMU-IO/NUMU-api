"""Daily digest — the agent's first proactive surface (Pillar 4).

Composes a store's "since yesterday" signals into a small, grounded payload the
panel greets the merchant with: orders + revenue for the trailing 24h, abandoned
carts still at stake, and products low on stock. Read-only, built on the same
repositories the read tools use — no rollups, no new tables. Each block carries a
suggested follow-up prompt so the panel can offer a one-tap next action.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.core.logging import get_logger
from src.infrastructure.repositories.abandoned_checkout_repository import (
    AbandonedCheckoutRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.product_repository import ProductRepository

logger = get_logger(__name__)

_LOW_STOCK_PREVIEW = 5


async def build_daily_digest(session, *, store_id: UUID, locale: str = "en") -> dict:
    """Return the store's proactive digest. Never raises — missing blocks are
    simply omitted so a single failing query can't blank the greeting."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    blocks: list[dict] = []

    # Orders + revenue in the trailing 24h.
    try:
        order_repo = OrderRepository(session)
        count = await order_repo.count_by_store(store_id, date_from=since, date_to=now)
        revenue_minor = await order_repo.get_revenue_by_date_range(store_id, since, now)
        if count:
            blocks.append({
                "kind": "orders",
                "count": count,
                "revenue": round(revenue_minor / 100, 2),
                "prompt": "How are my sales doing this week?",
            })
    except Exception as exc:  # noqa: BLE001 — a block failing must not blank the digest
        logger.warning("agent_digest_block_error", block="orders", error=str(exc))

    # Abandoned carts still recoverable (have contact, not recovered).
    try:
        carts, total = await AbandonedCheckoutRepository(session).list_by_store(
            store_id,
            skip=0,
            limit=100,
            abandoned_only=True,
            recovered_only=False,
            has_contact=True,
        )
        if total:
            value = round(sum(float(c.total) for c in carts), 2)
            blocks.append({
                "kind": "abandoned_carts",
                "count": total,
                "value_at_stake": value,
                "prompt": "Show my abandoned carts",
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_digest_block_error", block="carts", error=str(exc))

    # Products low on stock.
    try:
        low = await ProductRepository(session).get_low_stock(
            store_id, limit=_LOW_STOCK_PREVIEW + 1
        )
        if low:
            blocks.append({
                "kind": "low_stock",
                "count": len(low),
                "has_more": len(low) > _LOW_STOCK_PREVIEW,
                "items": [
                    {"id": str(p.id), "name": p.name, "quantity": p.quantity}
                    for p in low[:_LOW_STOCK_PREVIEW]
                ],
                "prompt": "Which products are low on stock?",
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_digest_block_error", block="low_stock", error=str(exc))

    return {
        "generated_at": now.isoformat(),
        "window_hours": 24,
        "blocks": blocks,
        "quiet": len(blocks) == 0,
    }
