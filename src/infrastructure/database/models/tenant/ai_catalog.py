"""Lamaa app (``ai-catalog``, «لمعة»): its four tables.

The app's backend lives in NUMU-api (docs/Plans/APPS/04-ai-catalog). It writes
product copy, SEO text, alt text and FAQs into NUMU's own product rows only
after the merchant accepts each field; these tables hold its settings, the
suggestions (one per provider call, so also the cost ledger and the undo
snapshot), bulk runs and the monthly allowance. None holds shopper data.
Uninstall purges all four after the retention window (``numu_apps.PURGERS``).
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


def _store_fk(**kw) -> Mapped[PyUUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("public.stores.id", ondelete="CASCADE"), **kw
    )


class AiCatalogSettingsModel(Base, TimestampMixin, TenantMixin):
    """One row per store, created with the defaults on the front's first call."""

    __tablename__ = "ai_catalog_settings"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = _store_fk(primary_key=True)
    arabic_style: Mapped[str] = mapped_column(String(8), nullable=False)
    languages: Mapped[list] = mapped_column(JSONB, nullable=False)
    store_facts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand_voice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    run_cap: Mapped[int | None] = mapped_column(Integer)


class AiCatalogBulkRunModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One bulk run. ``status``: ``running`` → ``review`` (every item written or
    failed) → ``applying`` → ``done``, or ``cancelled``."""

    __tablename__ = "ai_catalog_bulk_runs"
    __table_args__ = (
        Index("ix_ai_catalog_bulk_runs_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(nullable=False)
    fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True))


class AiCatalogSuggestionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One provider call for one product.

    ``status``: ``queued`` (a bulk item) → ``running`` → ``ready`` → ``applied``
    / ``dismissed`` / ``undone``, or ``failed`` / ``failed_verify``.
    ``current`` is NUMU's values when the call was made (the freshness check
    and the undo snapshot); ``applied`` is what the app wrote.
    """

    __tablename__ = "ai_catalog_suggestions"
    __table_args__ = (
        Index("ix_ai_catalog_suggestions_store_product", "store_id", "product_id"),
        Index("ix_ai_catalog_suggestions_run_status", "bulk_run_id", "status"),
        Index("ix_ai_catalog_suggestions_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(nullable=False)
    # SET NULL, not CASCADE: a product deleted mid-run keeps its bulk item,
    # which then fails and refunds its run; the daily cleanup removes it.
    product_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="SET NULL"),
    )
    bulk_run_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.ai_catalog_bulk_runs.id", ondelete="CASCADE"),
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False)
    fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    base_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    suggested: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    applied: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    applied_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32))


class AiCatalogUsageModel(Base, TenantMixin):
    """Provider calls and cost per store and month (``YYYY-MM``). A run is
    reserved here before every call and refunded when the provider fails."""

    __tablename__ = "ai_catalog_usage"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = _store_fk(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    runs_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
