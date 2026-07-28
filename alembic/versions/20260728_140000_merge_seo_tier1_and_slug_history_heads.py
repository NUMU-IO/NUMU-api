"""Merge the SEO-Tier-1 and slug-history heads.

Revision ID: merge_seo_slug_20260728
Revises: category_seo_20260728, previous_slugs_20260727
Create Date: 2026-07-28

Two SEO branches forked from ``rls_all_tenant_20260722`` on the same day and
landed independently:

  * ``previous_slugs_20260727`` — products/categories ``previous_slugs`` (dev)
  * ``category_seo_20260728``   — store SEO backfill, product ``brand``,
                                  category seo_title/description/image
                                  (this branch, via product_brand_20260728
                                  and seo_backfill_20260728)

Neither branch alone has two heads; the MERGE does, which is why CI caught it
and local ``alembic heads`` on either side did not. With two heads,
``alembic upgrade head`` aborts with "Multiple head revisions are present",
so the CI database never migrates at all — the Load Smoke job failed not on a
performance threshold but on ``relation "public.platform_config" does not
exist``, because the API could not boot.

The two lineages touch disjoint columns on the same two tables (previous_slugs
vs seo_title/seo_description/seo_image + brand), so there is no DDL conflict to
resolve — this converges them and nothing more.
"""

from collections.abc import Sequence

revision: str = "merge_seo_slug_20260728"
down_revision: tuple[str, str] = (
    "category_seo_20260728",
    "previous_slugs_20260727",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op: structural merge of two existing heads."""
    pass


def downgrade() -> None:
    """No-op: splits back into the two prior heads."""
    pass
