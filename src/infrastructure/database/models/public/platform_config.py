"""Platform configuration model (public schema).

Stores platform-wide configuration as key-value pairs with JSONB values.
Used for landing page section visibility and other global settings.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin

# Default landing page config — all sections visible
DEFAULT_LANDING_CONFIG = {
    "sections": {
        "hero": {"visible": True, "order": 0},
        "preview": {"visible": True, "order": 1},
        "features": {"visible": True, "order": 2},
        "import-showcase": {"visible": True, "order": 3},
        "ai-showcase": {"visible": True, "order": 4},
        "multichannel-showcase": {"visible": True, "order": 5},
        "integrations": {"visible": True, "order": 6},
        "testimonials": {"visible": True, "order": 7},
        "cta": {"visible": True, "order": 8},
        "footer": {"visible": True, "order": 9},
    }
}


class PlatformConfigModel(Base, TimestampMixin):
    """Platform-wide configuration stored as JSONB key-value pairs.

    Each row is a distinct config namespace (e.g. 'landing_page').
    This lives in the public schema since it's not tenant-scoped.
    """

    __tablename__ = "platform_config"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Platform-wide default theme reference (Session A 2026-05-27, file 04 §5).
    # Only the row keyed ``platform_default_theme`` is expected to hold a
    # non-NULL value; all other rows leave it NULL. Setting it null reverts
    # new-store seeding to the legacy V2 fallback (sawsaw/rabbit-safe — they
    # were created before this column existed and are never touched).
    default_marketplace_theme_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "public.marketplace_themes.id",  # schema-qualified — matches __table_args__
            ondelete="SET NULL",
            name="fk_platform_config_default_marketplace_theme",
        ),
        nullable=True,
    )
