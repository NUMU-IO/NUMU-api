"""Add meta_marketing to service_name_enum.

Adds a new ServiceName so the Marketing API token (ads_management,
ads_read, business_management) can live in its own service_credentials
row separate from the CAPI/Pixel token. Required to unblock the
"Promote on Meta" and Custom Audience sync flows for merchants whose
existing CAPI token was issued by Meta's "Conversions API Application"
and therefore has no Marketing API scopes.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "meta_marketing_20260527"
down_revision: str = "is_internal_20260723"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE service_name_enum ADD VALUE IF NOT EXISTS 'meta_marketing'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    pass
