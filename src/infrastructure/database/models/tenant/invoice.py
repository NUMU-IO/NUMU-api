"""Invoice database model for ETA e-invoicing."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.invoice import InvoiceStatus, InvoiceType
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class InvoiceModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Invoice database model for Egyptian e-invoicing (ETA)."""

    __tablename__ = "invoices"
    # invoice_number is unique per (store_id, invoice_number), not globally —
    # the per-store sequential numbering scheme would otherwise collide
    # across stores. See migration 8e1f6a2b4d5c.
    __table_args__ = (
        Index(
            "uq_invoices_store_invoice_number",
            "store_id",
            "invoice_number",
            unique=True,
        ),
        {"schema": "public"},
    )

    # Relationships
    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Invoice identification. Unique per store via composite index above.
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    internal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Type and status
    invoice_type: Mapped[InvoiceType] = mapped_column(
        Enum(
            InvoiceType,
            name="invoicetype",
            schema="public",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=InvoiceType.INVOICE,
        nullable=False,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(
            InvoiceStatus,
            name="invoicestatus",
            schema="public",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=InvoiceStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Dates
    date_issued: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Parties (stored as JSON)
    seller: Mapped[dict] = mapped_column(JSONB, nullable=False)
    buyer: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Currency
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    exchange_rate: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )  # Stored as cents (1.00 = 100)

    # Line items (stored as JSON array)
    line_items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    # Totals (all in cents) — VAT-inclusive model.
    # ``subtotal`` is the sum of line totals with VAT already included.
    # ``vat_amount`` is the VAT *extracted* from ``subtotal`` for tax
    # reporting (NOT added on top). ``grand_total`` is what the
    # customer actually pays: ``subtotal + shipping_fee - extra_discount``.
    subtotal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_taxes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # VAT-inclusive pricing fields (Phase: Egyptian retail VAT model).
    # ``prices_include_vat`` flags rows authored under this model so a
    # future migration to mixed-mode pricing has a clean discriminator.
    prices_include_vat: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("14.00")
    )
    vat_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_amount_before_vat: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    shipping_fee: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grand_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ETA submission details
    eta_uuid: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    eta_long_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    eta_submission_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    eta_internal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    eta_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    eta_status_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    eta_status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # QR Code
    qr_code_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_code_image: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # Base64 encoded

    # Signature
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signature_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # PDF storage
    pdf_r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Related documents
    related_invoice_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_invoice_number: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_ar: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    store = relationship("StoreModel", back_populates="invoices", lazy="selectin")
    order = relationship("OrderModel", back_populates="invoice", lazy="selectin")
    customer = relationship("CustomerModel", back_populates="invoices", lazy="selectin")
    related_invoice = relationship(
        "InvoiceModel", remote_side="InvoiceModel.id", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<InvoiceModel(id={self.id}, invoice_number={self.invoice_number})>"
