"""Add TikTok Shop sales-channel enums.

P7 of the TikTok integration. Extends the service enums so a TikTok Shop
OAuth token can be stored via the existing ``ServiceCredential`` pattern:

    service_type = SALES_CHANNEL
    service_name = TIKTOK_SHOP

Enum-only migration (the token lives in the existing ``service_credentials``
table; the shop connection metadata lives in ``store.settings.channels.tiktok_shop``
JSONB — no new table). ``ADD VALUE`` is safe inside Alembic's tx here because
the new values aren't referenced elsewhere in this migration.

Revision ID: tiktok_shop_20260701
Revises: tiktok_tracking_20260701
Create Date: 2026-07-01
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "tiktok_shop_20260701"
down_revision: str | None = "tiktok_tracking_20260701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE public.service_type_enum ADD VALUE IF NOT EXISTS 'sales_channel'"
    )
    op.execute(
        "ALTER TYPE public.service_name_enum ADD VALUE IF NOT EXISTS 'tiktok_shop'"
    )


def downgrade() -> None:
    # PostgreSQL can't drop enum values — harmless to leave them.
    pass
