"""Add campaign_id FK to coupons (campaign-attributed discounts).

Revision ID: campaign_coupon_fk_20260522
Revises: utm_campaign_attribution_20260521
Create Date: 2026-05-22

Lets merchants auto-issue per-campaign discount codes. When an order
redeems a coupon that carries ``campaign_id``, and the order doesn't
already have a UTM-resolved campaign attribution, the coupon's
campaign_id is stamped onto the order — closing the loop between
"customer used the code from my Eid email" and the campaign
performance dashboard.

* ``coupons.campaign_id`` — nullable FK on
  ``public.marketing_campaigns.id``, ON DELETE SET NULL so historical
  coupons keep working even if the campaign is later deleted.
* Partial index on the new column (``WHERE campaign_id IS NOT NULL``)
  — the lookup pattern is "list this campaign's coupons", which is
  bounded; most coupons are standalone and shouldn't bloat the index.

Lock impact: pure ADD COLUMN (nullable, no default) — Postgres treats
it as metadata-only. FK is added ``NOT VALID`` to skip the table scan
against historical rows that are all NULL by construction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "campaign_coupon_fk_20260522"
down_revision: str = "utm_campaign_attribution_20260521"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column(
            "campaign_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="public",
    )
    # NOT VALID FK — Postgres skips the table scan because existing
    # rows are NULL by construction (the column was just added). Future
    # rows are validated normally.
    op.execute(
        "ALTER TABLE public.coupons "
        "ADD CONSTRAINT fk_coupons_campaign_id "
        "FOREIGN KEY (campaign_id) REFERENCES public.marketing_campaigns(id) "
        "ON DELETE SET NULL NOT VALID"
    )
    # Partial index — most coupons are standalone, no need to index NULLs.
    op.create_index(
        "ix_coupons_campaign_id",
        "coupons",
        ["campaign_id"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_coupons_campaign_id", table_name="coupons", schema="public")
    op.drop_constraint(
        "fk_coupons_campaign_id", "coupons", type_="foreignkey", schema="public"
    )
    op.drop_column("coupons", "campaign_id", schema="public")
