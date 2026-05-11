"""Shopify subscription model — synced copy of the merchant's Shopify
Billing API subscription. Source of truth is Shopify; this row is
upserted by the Shopify-app's syncSubscriptionToNumu helper.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class ShopifySubscriptionModel(Base, UUIDMixin, TimestampMixin):
    """Per-store Shopify Billing API subscription record."""

    __tablename__ = "shopify_subscriptions"
    __table_args__ = (
        # A given Shopify subscription id is unique within a store. Allows
        # historic rows (cancelled/expired) to coexist with the current
        # active row when Shopify reuses the store across plan switches.
        UniqueConstraint(
            "store_id",
            "shopify_subscription_id",
            name="uq_shopify_subscription_store_sub",
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Shopify GraphQL global ID — e.g. "gid://shopify/AppSubscription/123".
    shopify_subscription_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Mirror of Shopify's AppSubscription.status enum.
    # ACTIVE | ACCEPTED | PENDING | DECLINED | EXPIRED | CANCELLED | FROZEN
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    # numu-trust-network plan id: starter | growth | scale.
    plan_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    is_trial: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Set the first time status transitions to a terminal state. Preserved
    # on subsequent retries so the audit timestamp is the earliest one.
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # When the Shopify-app last successfully posted this row. Used as a
    # liveness indicator for ops/monitoring.
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
