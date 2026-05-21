"""Customizer undo entries model — Phase 6."""

from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class CustomizerUndoEntryModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "customizer_undo_entries"
    __table_args__ = (
        # Hot path: list/prune by (user, store, theme), newest-first.
        Index(
            "ix_customizer_undo_user_scope",
            "user_id",
            "store_id",
            "theme_id",
            "created_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    theme_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_label: Mapped[str] = mapped_column(String(128), nullable=False)
    forward: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    inverse: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
