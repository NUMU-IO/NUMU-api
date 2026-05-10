"""Currency rates model — Phase 6.

Daily FX rates for multi-currency presentment. Composite primary key
on (base, target) — one row per direction. Refreshed daily via
Celery beat; the `fetched_at` column lets the storefront show "rates
as of <date>" copy if a merchant requires disclosure.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, PrimaryKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base


class CurrencyRateModel(Base):
    __tablename__ = "currency_rates"
    __table_args__ = (
        PrimaryKeyConstraint("base", "target", name="pk_currency_rates"),
        {"schema": "public"},
    )

    base: Mapped[str] = mapped_column(String(3), nullable=False)
    target: Mapped[str] = mapped_column(String(3), nullable=False)
    # Up to 18.10 — covers high-precision crypto rates if we ever add them.
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
