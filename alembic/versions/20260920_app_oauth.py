"""Connected Partner Apps: OAuth codes, app tokens, app-owned webhooks.

Revision ID: app_oauth_20260920
Revises: app_registry_20260919
Create Date: 2026-09-20

Phase 4 of docs/Plans/apps-developer-work. Additive:
- ``app_installations.status`` (``pending_auth | active``; existing rows are
  ``active``) and ``granted_scopes`` (what the merchant consented to);
- ``app_oauth_clients.client_secret_encrypted`` + ``secret_key_id``: the
  client secret signs app webhooks and "Open app" links, so NUMU must be able
  to read it back. Fernet via SecretsManager, never plaintext. Apps created
  before this have no encrypted copy until their secret is rotated;
- ``app_oauth_codes``: single-use authorization codes (10 minutes, hashed);
- ``app_access_tokens``: ``numu_app_`` tokens (sha256 hashed, like PATs);
- ``webhook_subscriptions.app_installation_id``: a subscription the app owns;
  deleted with the installation.

Row-level security on ``app_installations`` (a plan item) already exists:
ENABLE + FORCE with the tenant_isolation_* and admin_bypass policies, from
the generic tenant-table RLS migration. Nothing to add here.

Downgrade reverses all of it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "app_oauth_20260920"
down_revision: str | Sequence[str] | None = "app_registry_20260919"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_installations",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        schema="public",
    )
    op.add_column(
        "app_installations",
        sa.Column(
            "granted_scopes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="public",
    )
    op.add_column(
        "app_oauth_clients",
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
        schema="public",
    )
    op.add_column(
        "app_oauth_clients",
        sa.Column("secret_key_id", sa.String(100), nullable=True),
        schema="public",
    )

    op.create_table(
        "app_oauth_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.app_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.create_table(
        "app_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.app_installations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.add_column(
        "webhook_subscriptions",
        sa.Column(
            "app_installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.app_installations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema="public",
    )
    op.create_index(
        "ix_webhook_subscriptions_app_installation",
        "webhook_subscriptions",
        ["app_installation_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_subscriptions_app_installation",
        table_name="webhook_subscriptions",
        schema="public",
    )
    op.drop_column("webhook_subscriptions", "app_installation_id", schema="public")
    op.drop_table("app_access_tokens", schema="public")
    op.drop_table("app_oauth_codes", schema="public")
    op.drop_column("app_oauth_clients", "secret_key_id", schema="public")
    op.drop_column("app_oauth_clients", "client_secret_encrypted", schema="public")
    op.drop_column("app_installations", "granted_scopes", schema="public")
    op.drop_column("app_installations", "status", schema="public")
