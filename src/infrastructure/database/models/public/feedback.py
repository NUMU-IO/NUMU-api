"""Beta merchant feedback database model (public schema)."""

from sqlalchemy import Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.feedback import FeedbackCategory
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class FeedbackModel(Base, UUIDMixin, TimestampMixin):
    """Beta merchant feedback model.

    Public schema because feedback aggregation spans tenants.
    """

    __tablename__ = "feedback"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    category: Mapped[FeedbackCategory] = mapped_column(
        Enum(FeedbackCategory, name="feedbackcategory", schema="public"),
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    contact_ok: Mapped[bool] = mapped_column(default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<FeedbackModel(id={self.id}, category={self.category})>"
