"""Marketing campaign audience resolver.

Translates a structured audience filter into a customer list for an
email/SMS campaign send. Used both by:

  * The hub's pre-send **estimate** endpoint (so the merchant sees
    "Will send to ~327 recipients" before clicking Send), and
  * The dispatcher (so the same filter chosen at create-time decides
    who actually gets the message at send-time).

Why a dedicated module instead of reusing whatsapp_campaigns'
``_resolve_audience``: the WhatsApp helper hard-codes a phone-only
contact filter, only supports two date-range fields, and returns a
dict-shape tailored to the WhatsApp dispatcher. Marketing campaigns
need the channel-aware contact filter (email for EMAIL channel,
phone for SMS), additional spend/order/tag filters, and the
``accepts_marketing`` gate that legal requires for non-WhatsApp
broadcasts.

Filter schema is intentionally flat + JSON-friendly so it survives
the round-trip through ``marketing_campaigns.audience_filter`` (a
JSONB column on the campaign row).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.marketing_campaign import CampaignChannel
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.order import OrderModel

# Named presets the hub renders as one-click chips. Each preset translates
# to a concrete filter dict — kept in the resolver (not the schema) so
# changing a preset's definition doesn't require a schema migration.
PresetKey = Literal[
    "all_opted_in",
    "high_value",
    "recent_buyers",
    "lapsed",
    "new_customers",
]

_PRESETS: dict[str, dict] = {
    "all_opted_in": {"accepts_marketing": True},
    "high_value": {
        "accepts_marketing": True,
        "min_total_spent_cents": 500_000,  # EGP 5,000+
    },
    "recent_buyers": {
        "accepts_marketing": True,
        "ordered_within_days": 30,
    },
    "lapsed": {
        "accepts_marketing": True,
        "inactive_days": 90,
        "min_total_orders": 1,
    },
    "new_customers": {
        "accepts_marketing": True,
        "max_total_orders": 1,
        "created_within_days": 30,
    },
}


class MarketingAudienceFilter(BaseModel):
    """Audience filter for marketing campaigns.

    Two modes:
      * **preset** — pick a named preset; preset_overrides apply on top
      * **custom** — leave preset empty and supply the field filters directly

    All field filters are AND'd together. Missing fields = no filter on
    that dimension.
    """

    # ---- Preset (optional) ----
    preset: PresetKey | None = None

    # ---- Consent ----
    # Default True — legal requirement for non-transactional broadcasts.
    # Setting False overrides for stores running internal admin-test sends.
    accepts_marketing: bool = True

    # ---- Activity / recency ----
    ordered_within_days: int | None = Field(default=None, ge=1, le=3650)
    inactive_days: int | None = Field(default=None, ge=1, le=3650)
    created_within_days: int | None = Field(default=None, ge=1, le=3650)

    # ---- Lifetime value ----
    min_total_spent_cents: int | None = Field(default=None, ge=0)
    max_total_spent_cents: int | None = Field(default=None, ge=0)
    min_total_orders: int | None = Field(default=None, ge=0)
    max_total_orders: int | None = Field(default=None, ge=0)

    # ---- Tags (any-of match) ----
    tags_any: list[str] | None = None


class AudienceSample(BaseModel):
    """One row in the recipient-preview list."""

    id: UUID
    name: str
    contact: str  # email when channel=EMAIL, phone when channel=SMS


class AudienceEstimate(BaseModel):
    """Pre-send audience count + sample for the hub's preview panel."""

    estimated_count: int
    sample: list[AudienceSample] = Field(default_factory=list)


def _materialize(filter_in: MarketingAudienceFilter) -> dict:
    """Apply preset (if any) + overrides → flat filter dict.

    Custom field values on the request take precedence over the preset's
    defaults so the merchant can pick "Lapsed" and then bump
    ``inactive_days`` to 120 without losing the preset's other filters.
    """
    raw = filter_in.model_dump(exclude_none=True, exclude={"preset"})
    if filter_in.preset and filter_in.preset in _PRESETS:
        base = dict(_PRESETS[filter_in.preset])
        base.update(raw)  # request overrides preset
        return base
    return raw


def _build_query(
    store_id: UUID,
    filter_in: MarketingAudienceFilter,
    channel: CampaignChannel,
):
    """Compose the customer-filter SQLAlchemy ``select`` core.

    Channel decides which contact column is required-not-null:
    EMAIL→email, SMS→phone. Caller adds its own projection + limit.
    """
    f = _materialize(filter_in)
    contact_col = (
        CustomerModel.email if channel == CampaignChannel.EMAIL else CustomerModel.phone
    )

    stmt = select(CustomerModel).where(
        CustomerModel.store_id == store_id,
        contact_col.is_not(None),
        contact_col != "",
    )

    if f.get("accepts_marketing", True):
        stmt = stmt.where(CustomerModel.accepts_marketing.is_(True))

    if "min_total_spent_cents" in f:
        stmt = stmt.where(CustomerModel.total_spent >= f["min_total_spent_cents"])
    if "max_total_spent_cents" in f:
        stmt = stmt.where(CustomerModel.total_spent <= f["max_total_spent_cents"])
    if "min_total_orders" in f:
        stmt = stmt.where(CustomerModel.total_orders >= f["min_total_orders"])
    if "max_total_orders" in f:
        stmt = stmt.where(CustomerModel.total_orders <= f["max_total_orders"])

    now = datetime.now(UTC)

    if "created_within_days" in f:
        cutoff = now - timedelta(days=f["created_within_days"])
        stmt = stmt.where(CustomerModel.created_at >= cutoff)

    if "ordered_within_days" in f:
        cutoff = now - timedelta(days=f["ordered_within_days"])
        recent_buyers = (
            select(OrderModel.customer_id)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= cutoff,
                OrderModel.customer_id.is_not(None),
            )
            .distinct()
        )
        stmt = stmt.where(CustomerModel.id.in_(recent_buyers))

    if "inactive_days" in f:
        cutoff = now - timedelta(days=f["inactive_days"])
        recent_active = (
            select(OrderModel.customer_id)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= cutoff,
                OrderModel.customer_id.is_not(None),
            )
            .distinct()
        )
        stmt = stmt.where(~CustomerModel.id.in_(recent_active))

    tags = f.get("tags_any")
    if tags:
        # JSONB array overlap: ``customers.tags && ARRAY['vip','wholesale']``
        stmt = stmt.where(CustomerModel.tags.op("&&")(tags))

    return stmt


async def count_audience(
    db: AsyncSession,
    *,
    store_id: UUID,
    filter_in: MarketingAudienceFilter,
    channel: CampaignChannel,
) -> int:
    """Count customers that would be targeted by this filter."""
    stmt = _build_query(store_id, filter_in, channel)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    return (await db.execute(count_stmt)).scalar_one() or 0


async def resolve_audience(
    db: AsyncSession,
    *,
    store_id: UUID,
    filter_in: MarketingAudienceFilter,
    channel: CampaignChannel,
    limit: int | None = None,
) -> list[CustomerModel]:
    """Return the actual customer rows the campaign should send to."""
    stmt = _build_query(store_id, filter_in, channel)
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def estimate_audience(
    db: AsyncSession,
    *,
    store_id: UUID,
    filter_in: MarketingAudienceFilter,
    channel: CampaignChannel,
    sample_size: int = 5,
) -> AudienceEstimate:
    """Combined count + sample for the hub's pre-send preview."""
    total = await count_audience(
        db, store_id=store_id, filter_in=filter_in, channel=channel
    )
    sample_customers = await resolve_audience(
        db,
        store_id=store_id,
        filter_in=filter_in,
        channel=channel,
        limit=sample_size,
    )
    contact_attr = "email" if channel == CampaignChannel.EMAIL else "phone"
    sample = [
        AudienceSample(
            id=c.id,
            name=f"{c.first_name or ''} {c.last_name or ''}".strip() or "Customer",
            contact=getattr(c, contact_attr) or "",
        )
        for c in sample_customers
    ]
    return AudienceEstimate(estimated_count=total, sample=sample)
