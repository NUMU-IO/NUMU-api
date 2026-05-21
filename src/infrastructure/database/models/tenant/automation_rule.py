"""Automation rule model — defines automated actions triggered by events."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class AutomationRuleModel(Base, UUIDMixin, TimestampMixin):
    """Automation rule definition."""

    __tablename__ = "automation_rules"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    trigger_event: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    conditions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'[]'",
    )
    actions: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'[]'",
    )
    times_triggered: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
