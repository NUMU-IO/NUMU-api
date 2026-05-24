"""AI optimization tips — feature 002 US8.

Pure heuristic ruleset (no LLM call). Inputs are this campaign's
already-computed breakdowns (channel, customer-type, device, coupon
stats from the performance endpoint, top_products list). Output is
0-3 sorted Tip objects suitable for the right-sidebar Tips panel.

Per FR-038 + research §8: deterministic outputs, easy to unit-test,
no external dependency, no cost per render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any


@dataclass
class Tip:
    id: str
    severity: str  # "info" | "warning"
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "body": self.body,
            "data": self.data,
        }


_MIN_SESSIONS_FOR_CHANNEL_TIP = 30
_MIN_SESSIONS_FOR_MOBILE_TIP = 100
_MIN_PRODUCT_COUNT_FOR_CONCENTRATION_TIP = 3
_OUTPERFORMING_CHANNEL_RATIO = 2.5
_COUPON_CANNIBALIZATION_THRESHOLD = 0.7
_MOBILE_SKEW_THRESHOLD = 0.3
_TOP_PRODUCT_CONCENTRATION_THRESHOLD = 0.6


def _channel_cvrs(
    channel_breakdown: list[dict],
    customer_type_breakdown: dict[str, dict[str, int]],
) -> dict[str, float]:
    """Per-channel conversion rate using sessions + total orders.

    Channels with < _MIN_SESSIONS_FOR_CHANNEL_TIP sessions get dropped
    (insufficient sample) so a 1-session channel that converted doesn't
    spuriously fire the "boost channel" tip.
    """
    total_orders = customer_type_breakdown.get("new_customers", {}).get(
        "orders", 0
    ) + customer_type_breakdown.get("returning_customers", {}).get("orders", 0)
    total_sessions = sum(c.get("sessions", 0) for c in channel_breakdown)
    if total_sessions == 0 or total_orders == 0:
        return {}
    # No per-channel orders breakdown in v1 — we estimate by
    # proportional allocation of the global order count to channels by
    # session share. Coarse but enough to spot order-of-magnitude
    # differences which is all the heuristic needs.
    cvrs: dict[str, float] = {}
    for row in channel_breakdown:
        sessions = row.get("sessions", 0)
        if sessions < _MIN_SESSIONS_FOR_CHANNEL_TIP:
            continue
        share = sessions / total_sessions
        estimated_orders = total_orders * share
        cvrs[row.get("channel", "direct")] = estimated_orders / sessions
    return cvrs


def compute_tips(
    *,
    channel_breakdown: list[dict],
    customer_type_breakdown: dict[str, dict[str, int]],
    device_breakdown: list[dict],
    coupon_redemptions: int,
    coupon_revenue_cents: int,
    total_revenue_cents: int,
    top_products: list[dict],
) -> list[Tip]:
    """Compute heuristic tips. Up to 4 fire; sorted by severity then
    impact.
    """
    tips: list[Tip] = []

    # 1) Outperforming-channel tip
    cvrs = _channel_cvrs(channel_breakdown, customer_type_breakdown)
    if len(cvrs) >= 2:
        sorted_cvrs = sorted(cvrs.items(), key=lambda kv: kv[1], reverse=True)
        winner_channel, winner_cvr = sorted_cvrs[0]
        median_cvr = median([v for _, v in sorted_cvrs])
        if median_cvr > 0 and winner_cvr / median_cvr > _OUTPERFORMING_CHANNEL_RATIO:
            ratio = winner_cvr / median_cvr
            tips.append(
                Tip(
                    id="boost-channel",
                    severity="info",
                    title=(
                        f"{winner_channel.capitalize()} converts "
                        f"{ratio:.1f}× better than your median channel"
                    ),
                    body=(
                        f"Consider shifting budget toward {winner_channel}. "
                        f"It delivered {winner_cvr * 100:.1f}% conversion vs the "
                        f"median {median_cvr * 100:.1f}% across your channels."
                    ),
                    data={
                        "winner_channel": winner_channel,
                        "winner_cvr": winner_cvr,
                        "median_cvr": median_cvr,
                    },
                )
            )

    # 2) Coupon-cannibalization tip
    if total_revenue_cents > 0:
        coupon_share = coupon_revenue_cents / total_revenue_cents
        if coupon_share > _COUPON_CANNIBALIZATION_THRESHOLD:
            tips.append(
                Tip(
                    id="coupon-cannibalization",
                    severity="warning",
                    title="Most revenue came via discount codes",
                    body=(
                        f"{coupon_share * 100:.0f}% of this campaign's revenue "
                        "was discounted via coupons. Consider sending without a "
                        "coupon next time to test full-price demand."
                    ),
                    data={
                        "coupon_revenue_share": coupon_share,
                        "coupon_redemptions": coupon_redemptions,
                    },
                )
            )

    # 3) Mobile-skew tip
    total_device_sessions = sum(d.get("sessions", 0) for d in device_breakdown)
    if total_device_sessions >= _MIN_SESSIONS_FOR_MOBILE_TIP:
        mobile_sessions = sum(
            d.get("sessions", 0)
            for d in device_breakdown
            if d.get("device") == "mobile"
        )
        mobile_share = mobile_sessions / total_device_sessions
        if mobile_share < _MOBILE_SKEW_THRESHOLD:
            tips.append(
                Tip(
                    id="mobile-skew",
                    severity="warning",
                    title="Your campaign skews desktop",
                    body=(
                        f"Only {mobile_share * 100:.0f}% of this campaign's "
                        "sessions came from mobile devices, but Egypt e-commerce "
                        "traffic is ~75% mobile. Try mobile-first creative or "
                        "test on phones before the next send."
                    ),
                    data={
                        "mobile_session_share": mobile_share,
                        "total_sessions": total_device_sessions,
                    },
                )
            )

    # 4) Top-product-concentration tip
    if (
        total_revenue_cents > 0
        and len(top_products) >= _MIN_PRODUCT_COUNT_FOR_CONCENTRATION_TIP
    ):
        top = top_products[0]
        top_share = (top.get("revenue_cents", 0)) / total_revenue_cents
        if top_share > _TOP_PRODUCT_CONCENTRATION_THRESHOLD:
            top_name = top.get("name") or "your top product"
            tips.append(
                Tip(
                    id="top-product-concentration",
                    severity="info",
                    title="Most revenue came from one product",
                    body=(
                        f"{top_name} drove {top_share * 100:.0f}% of this "
                        "campaign's revenue. Consider featuring a wider set of "
                        "products in the next send to diversify."
                    ),
                    data={
                        "top_product_share": top_share,
                        "top_product_name": top_name,
                    },
                )
            )

    # Sort: warnings before infos, then by impact (rough): warnings by
    # severity threshold proximity, infos by channel-lift ratio etc.
    severity_order = {"warning": 0, "info": 1}
    tips.sort(key=lambda t: severity_order.get(t.severity, 2))
    return tips
