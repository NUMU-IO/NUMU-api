"""Category database model."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class CategoryModel(Base, UUIDMixin, TimestampMixin):
    """Category database model."""

    __tablename__ = "categories"

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships
    store = relationship("StoreModel", back_populates="categories", lazy="selectin")
    products = relationship("ProductModel", back_populates="category", lazy="selectin")
    parent = relationship("CategoryModel", remote_side="CategoryModel.id", lazy="selectin")

    def __repr__(self) -> str:
        return f"<CategoryModel(id={self.id}, name={self.name})>"
