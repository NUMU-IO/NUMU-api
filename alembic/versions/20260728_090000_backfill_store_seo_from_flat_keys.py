"""Backfill store.settings.seo from the legacy top-level seo_* keys.

The hub's Preferences page wrote seo_title/seo_description/social_image_url at
the TOP level of settings while the storefront reads only settings.seo, so that
input was never served. Copies them into the typed block.

Only fills fields that are absent or JSON-null — never clobbers a merchant
value. Truncates to the typed limits (70/160) rather than skipping the row.
Legacy keys are left in place; removing them is a separate cleanup. Idempotent.

Truncation is duplicated from store_seo._truncate on purpose: a migration must
stay frozen at write time.

Revision ID: seo_backfill_20260728
Revises: rls_all_tenant_20260722
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "seo_backfill_20260728"
down_revision: str | None = "rls_all_tenant_20260722"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (legacy key, typed field, max_length) as of this migration.
_FIELDS = (
    ("seo_title", "seo_title", 70),
    ("seo_description", "seo_description", 160),
    ("social_image_url", "social_image_url", 2048),
)


def _truncated_sql(legacy_key: str, limit: int) -> str:
    """Cut at the last space inside the budget, unless that loses >40% of it."""
    src = f"btrim(settings ->> '{legacy_key}')"
    clipped = f"left({src}, {limit})"
    worded = f"btrim(regexp_replace({clipped}, '\\s+\\S*$', ''))"
    return f"""
        CASE
            WHEN length({src}) <= {limit} THEN {src}
            WHEN length({worded}) >= {int(limit * 0.6)} THEN {worded}
            ELSE btrim({clipped})
        END
    """


def _legacy_present(legacy_key: str) -> str:
    return f"""
        jsonb_typeof(settings -> '{legacy_key}') = 'string'
        AND btrim(settings ->> '{legacy_key}') <> ''
    """


def _typed_empty(field: str) -> str:
    # Missing key → SQL NULL; explicit JSON null → 'null'. Both are safe to fill.
    return f"""
        (
            settings -> 'seo' -> '{field}' IS NULL
            OR jsonb_typeof(settings -> 'seo' -> '{field}') = 'null'
        )
    """


def upgrade() -> None:
    # jsonb_set creates only the final key of a path, never intermediates, so
    # `{seo,seo_title}` would no-op on a store with no `seo` block — most of them.
    op.execute(
        """
        UPDATE public.stores
           SET settings = jsonb_set(
                   COALESCE(settings, '{}'::jsonb), '{seo}', '{}'::jsonb
               )
         WHERE COALESCE(settings, '{}'::jsonb) -> 'seo' IS NULL
            OR jsonb_typeof(COALESCE(settings, '{}'::jsonb) -> 'seo') <> 'object'
        """
    )

    for legacy_key, field, limit in _FIELDS:
        op.execute(
            f"""
            UPDATE public.stores
               SET settings = jsonb_set(
                       settings,
                       '{{seo,{field}}}',
                       to_jsonb({_truncated_sql(legacy_key, limit)})
                   )
             WHERE {_legacy_present(legacy_key)}
               AND {_typed_empty(field)}
            """
        )


def downgrade() -> None:
    # Strip only while the typed field still equals what we wrote, so merchant
    # edits made since the upgrade survive.
    for legacy_key, field, limit in _FIELDS:
        op.execute(
            f"""
            UPDATE public.stores
               SET settings = jsonb_set(
                       settings, '{{seo}}', (settings -> 'seo') - '{field}'
                   )
             WHERE jsonb_typeof(settings -> 'seo') = 'object'
               AND {_legacy_present(legacy_key)}
               AND settings -> 'seo' ->> '{field}' = {_truncated_sql(legacy_key, limit)}
            """
        )
