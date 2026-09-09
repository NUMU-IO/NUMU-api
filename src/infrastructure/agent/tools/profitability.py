"""`get_profitability` read tool — what the store actually kept.

AUTO-tier read. Revenue and cost of goods come from the store's own rows:
revenue from what each order collected, COGS from `products.cost_price` against
the quantities in `orders.line_items`. Spend the platform does not hold — ad
budgets, courier invoices, packaging — is passed in by the merchant, because
that is how they ask the question: "what did I make on the last month, the
products cost me X and I spent Y on ads".

Two rules keep the answer honest:

* Costs the platform holds and costs the merchant states are reported
  separately, never blended into one unexplained figure.
* A product with no `cost_price` has no known cost, and counting it as zero
  would silently inflate profit. Those lines are counted and reported as
  coverage, so the model can say "this covers 8 of 11 items" instead of
  presenting a number that is quietly wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from src.application.agent.tools import ToolContext, ToolResult
from src.core.agent.entities import RiskTier
from src.core.logging import get_logger

logger = get_logger(__name__)

REQUIRED_PERMISSION = "analytics.view"

_MAX_DAYS = 365
_DEFAULT_DAYS = 30

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "days": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_DAYS,
            "description": "Window in days, ending now. Defaults to 30.",
        },
        "marketing_spend": {
            "type": "number",
            "minimum": 0,
            "description": (
                "What the merchant says they spent on ads or campaigns in this "
                "window, in store currency. The platform does not record ad "
                "spend, so pass it only when the merchant states a figure."
            ),
        },
        "other_costs": {
            "type": "number",
            "minimum": 0,
            "description": (
                "Any other cost the merchant states for this window — courier "
                "invoices, packaging, staff. Store currency."
            ),
        },
    },
    "additionalProperties": False,
}

# Revenue counts what was actually collected where that is recorded (partial
# COD acceptance writes collected_total), and excludes orders that never became
# money. COGS unnests line_items and joins each line to its product's cost.
_SQL = """
WITH scoped AS (
    SELECT id, COALESCE(collected_total, total) AS revenue, line_items
    FROM public.orders
    WHERE store_id = :sid
      AND created_at >= :since
      AND status NOT IN ('CANCELLED', 'REFUNDED', 'DRAFT')
),
lines AS (
    SELECT
        (li ->> 'product_id')::uuid AS product_id,
        COALESCE((li ->> 'quantity')::int, 0) AS quantity
    FROM scoped, LATERAL jsonb_array_elements(scoped.line_items) AS li
    WHERE li ->> 'product_id' IS NOT NULL
),
costed AS (
    SELECT l.quantity, p.cost_price
    FROM lines l
    LEFT JOIN public.products p ON p.id = l.product_id
)
SELECT
    (SELECT COUNT(*) FROM scoped)                                    AS orders_count,
    (SELECT COALESCE(SUM(revenue), 0) FROM scoped)                   AS revenue_cents,
    COALESCE(SUM(quantity * cost_price) FILTER (WHERE cost_price IS NOT NULL), 0)
                                                                     AS cogs_cents,
    COUNT(*) FILTER (WHERE cost_price IS NOT NULL)                   AS lines_with_cost,
    COUNT(*)                                                         AS lines_total
FROM costed
"""


def _money(cents: int | None) -> float:
    return round((cents or 0) / 100, 2)


async def get_profitability(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.has_permission and not await ctx.has_permission(REQUIRED_PERMISSION):
        return ToolResult.forbidden(REQUIRED_PERMISSION)

    days = int(args.get("days") or _DEFAULT_DAYS)
    days = max(1, min(days, _MAX_DAYS))
    since = datetime.now(UTC) - timedelta(days=days)

    row = (
        await ctx.session.execute(
            text(_SQL), {"sid": str(ctx.store_id), "since": since}
        )
    ).one()

    revenue = _money(row.revenue_cents)
    cogs = _money(row.cogs_cents)
    gross = round(revenue - cogs, 2)

    marketing = round(float(args.get("marketing_spend") or 0), 2)
    other = round(float(args.get("other_costs") or 0), 2)
    net = round(gross - marketing - other, 2)

    lines_total = int(row.lines_total or 0)
    lines_with_cost = int(row.lines_with_cost or 0)

    data = {
        "window_days": days,
        "orders_count": int(row.orders_count or 0),
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross,
        "gross_margin_pct": round(gross / revenue * 100, 1) if revenue else None,
        # Echoed back so the reply can show the merchant's own figures rather
        # than folding them into a total they cannot take apart.
        "merchant_stated_costs": {"marketing_spend": marketing, "other": other},
        "net_profit": net,
        "cost_coverage": {
            "lines_with_cost": lines_with_cost,
            "lines_total": lines_total,
            "complete": lines_total > 0 and lines_with_cost == lines_total,
        },
    }

    logger.info(
        "agent_profitability",
        store_id=str(ctx.store_id),
        days=days,
        orders=data["orders_count"],
        coverage=f"{lines_with_cost}/{lines_total}",
    )
    return ToolResult(
        ok=True,
        data=data,
        source=[{"type": "orders", "id": str(ctx.store_id)}],
    )


SPEC = {
    "name": "get_profitability",
    "description": (
        "Work out what the store actually kept over a window: revenue from its "
        "orders, cost of goods from each product's cost price, gross profit and "
        "margin. Pass marketing_spend and other_costs ONLY when the merchant "
        "states them — the platform does not record ad spend or courier "
        "invoices. Use for 'what is my net profit', 'am I making money on "
        "these orders', 'I spent 2000 on ads, was it worth it'. Always report "
        "cost_coverage: when it is not complete, some products have no cost "
        "price recorded and the profit shown is an over-estimate — say so."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": REQUIRED_PERMISSION,
    "executor": get_profitability,
}
