"""Dead-letter Celery task model (Phase 5.3)."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.dead_letter import DeadLetterStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TimestampMixin,
    UUIDMixin,
)


class DeadLetterEntryModel(Base, UUIDMixin, TimestampMixin):
    """Persistent record of a Celery task that exhausted its retries."""

    __tablename__ = "celery_dead_letters"
    __table_args__ = (
        Index("ix_celery_dead_letters_status", "status"),
        Index(
            "ix_celery_dead_letters_pending",
            "tenant_id",
            "task_name",
            postgresql_where=("status = 'pending'"),
        ),
        {"schema": "public"},
    )

    # tenant_id is nullable here unlike most other tenant-scoped tables
    # — platform-wide tasks (backups, marketplace catalog rebuilds)
    # have no tenant. Don't extend TenantMixin for that reason.
    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    store_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    task_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    args: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    kwargs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    queue: Mapped[str | None] = mapped_column(String(50), nullable=True)

    status: Mapped[DeadLetterStatus] = mapped_column(
        Enum(DeadLetterStatus, name="deadletterstatus", schema="public"),
        default=DeadLetterStatus.PENDING,
        nullable=False,
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    retried_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retried_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    retry_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
