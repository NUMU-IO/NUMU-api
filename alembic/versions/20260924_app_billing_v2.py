"""Paid apps v2: free trials and usage charges.

Revision ID: app_billing_v2_20260924
Revises: kashier_card_token_20260923
Create Date: 2026-09-24

Additive and idempotent (IF NOT EXISTS), public schema without RLS like
``app_subscriptions``:
- ``app_subscriptions``: ``is_trial`` (the current period was free) and the
  usage pricing the merchant approved (``usage_cap_cents``,
  ``usage_unit_cents``);
- ``app_trials``: one row per store and app that has had its free trial.
  Not deleted with the installation, so a reinstall gets no second trial;
- ``app_usage_records``: each metered charge an app reported, already taken
  from the wallet, unique per installation and idempotency key.

Downgrade drops the tables and columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "app_billing_v2_20260924"
down_revision: str | None = "kashier_card_token_20260923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.app_subscriptions "
        "ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT false, "
        "ADD COLUMN IF NOT EXISTS usage_cap_cents INTEGER, "
        "ADD COLUMN IF NOT EXISTS usage_unit_cents INTEGER"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_trials (
            store_id UUID NOT NULL,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (store_id, app_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_usage_records (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            store_id UUID NOT NULL,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            installation_id UUID NOT NULL
                REFERENCES public.app_installations(id) ON DELETE CASCADE,
            subscription_id UUID NOT NULL
                REFERENCES public.app_subscriptions(id) ON DELETE CASCADE,
            period_start TIMESTAMPTZ NOT NULL,
            units INTEGER,
            amount_cents INTEGER NOT NULL,
            description VARCHAR(255) NOT NULL,
            idempotency_key VARCHAR(100) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_usage_idempotency
                UNIQUE (installation_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_usage_sub_period "
        "ON public.app_usage_records (subscription_id, period_start)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.app_usage_records")
    op.execute("DROP TABLE IF EXISTS public.app_trials")
    op.execute(
        "ALTER TABLE public.app_subscriptions "
        "DROP COLUMN IF EXISTS usage_unit_cents, "
        "DROP COLUMN IF EXISTS usage_cap_cents, "
        "DROP COLUMN IF EXISTS is_trial"
    )
