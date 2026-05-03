"""Theme admin config (public schema).

Per-theme global flags controlled by platform admins:
* whether the theme is shown in the merchant theme picker (``is_visible``);
* the minimum tenant plan required to activate it (``required_plan``);
* sort order in the merchant grid (``display_order``).

The catalog of theme slugs is the static ``AVAILABLE_THEMES`` list in
``src.api.v1.routes.storefront.public`` — this table only stores the
admin-controlled flags keyed by slug. Rows are seeded at migration time and
auto-upserted by ``GET /admin/themes`` so adding a new slug to
``AVAILABLE_THEMES`` doesn't require a fresh migration.
"""

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin


class ThemeAdminConfigModel(Base, TimestampMixin):
    __tablename__ = "theme_admin_config"
    __table_args__ = (
        Index("ix_theme_admin_config_display_order", "display_order"),
        Index("ix_theme_admin_config_visible", "is_visible"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_slug: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # App-level Pydantic validation enforces Literal["free","starter","pro","enterprise"];
    # stored as String(20) so we can add tiers without a Postgres-enum migration.
    required_plan: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="free"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100"
    )
