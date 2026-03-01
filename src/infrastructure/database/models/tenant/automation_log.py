"""Automation log model — records automation rule execution history."""

from uuid import UUID as PyUUID

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class AutomationLogModel(Base, UUIDMixin, TimestampMixin):
    """Automation execution log entry."""

    __tablename__ = "automation_logs"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    rule_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    order_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    trigger_event: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    actions_executed: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'[]'",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    error_details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
