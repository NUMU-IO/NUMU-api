"""Migrate legacy `store.settings.shipping.zones[]` blobs into structured rows.

For every store with an existing legacy shipping config, convert its
free-text zone entries into rows in `shipping_zones` +
`shipping_zone_governorates` + `shipping_rates`.

Best-effort resolver (reuses the application layer's
`resolve_governorate` helper): case-insensitive English match, Arabic
exact match, transliteration alias map. Unresolved tokens don't crash
the migration — they're recorded under
`store.settings.shipping.legacy_migration_report` as
`{"unresolved": ["token1", "token2", ...]}` so the merchant dashboard
can surface them as a banner.

The legacy blob is preserved at `store.settings.shipping._legacy_zones`
for rollback safety. A follow-up migration after a quiescence period
will drop it.

Revision ID: migrate_ship_legacy_20260424
Revises: shipping_zones_20260424
Create Date: 2026-04-24 12:30:00.000000

Note: revision_id shortened from the original
`migrate_legacy_shipping_zones_20260424` to fit Alembic's
`version_num VARCHAR(32)` column. The data migration's upgrade body is
idempotent (re-run finds no stores with `settings.shipping.zones`
because we moved them under `_legacy_zones`), so the rename itself is
safe even if a prior attempt committed partial writes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op
from src.core.value_objects.geography import resolve_governorate

revision: str = "migrate_ship_legacy_20260424"
down_revision: str | None = "shipping_zones_20260424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _parse_governorates_field(raw: object) -> list[str]:
    """Legacy `zones[].governorates` was a comma-separated string OR a
    JSON array OR a malformed dict. Normalize to a list of raw tokens
    (still non-canonical).
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def upgrade() -> None:
    """Convert every store's legacy shipping settings into structured rows.

    Executed as a single transaction (Alembic default). Touches:
        * shipping_zones            — insert one row per legacy zone
        * shipping_zone_governorates — insert M2M rows
        * shipping_rates            — insert one flat rate per zone
        * stores.settings           — relocate legacy zones to
          `_legacy_zones` and add `legacy_migration_report`

    RLS: the shipping tables have RLS + admin_bypass; the migration
    runs under the superuser, which ignores RLS by default, so no
    tenant_id context is required. We still write tenant_id / store_id
    to every row.
    """
    conn = op.get_bind()

    stores = conn.execute(
        sa.text(
            """
            SELECT id, tenant_id, settings
            FROM public.stores
            WHERE settings IS NOT NULL
              AND settings -> 'shipping' -> 'zones' IS NOT NULL
            """
        )
    ).fetchall()

    for row in stores:
        store_id = row.id
        tenant_id = row.tenant_id
        settings = row.settings or {}
        shipping = settings.get("shipping") or {}
        legacy_zones = shipping.get("zones") or []
        if not legacy_zones:
            continue

        all_unresolved: list[str] = []
        zones_created = 0
        # Track covered governorates inside THIS migration run to catch
        # merchants whose legacy zones overlap (e.g. "Cairo" listed in
        # two zones). We keep only the first occurrence; others go to
        # the unresolved list with a tag.
        already_assigned: set[str] = set()

        for idx, legacy in enumerate(legacy_zones):
            zone_name = str(legacy.get("zone") or f"Zone {idx + 1}").strip() or (
                f"Zone {idx + 1}"
            )
            tokens = _parse_governorates_field(legacy.get("governorates"))
            rate_egp = legacy.get("rate") or 0.0
            try:
                rate_cents = int(round(float(rate_egp) * 100))
            except (TypeError, ValueError):
                rate_cents = 0

            gov_codes: list[str] = []
            for token in tokens:
                gov = resolve_governorate(token)
                if gov is None:
                    all_unresolved.append(token)
                    continue
                if gov.code in already_assigned:
                    all_unresolved.append(f"{token} (duplicate → already assigned)")
                    continue
                gov_codes.append(gov.code)
                already_assigned.add(gov.code)

            if not gov_codes:
                # Nothing we could resolve for this zone; skip creating
                # the row. Tokens are already in all_unresolved.
                continue

            zone_id = uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO public.shipping_zones
                        (id, tenant_id, store_id, name, estimated_days_min,
                         estimated_days_max, cod_enabled, cod_fee_cents,
                         is_active, sort_order)
                    VALUES
                        (:id, :tenant_id, :store_id, :name, :min_days,
                         :max_days, TRUE, 0, TRUE, :sort_order)
                    """
                ),
                {
                    "id": str(zone_id),
                    "tenant_id": str(tenant_id),
                    "store_id": str(store_id),
                    "name": zone_name[:100],
                    "min_days": 2,
                    "max_days": 5,
                    "sort_order": idx,
                },
            )
            for code in gov_codes:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO public.shipping_zone_governorates
                            (zone_id, governorate_code, tenant_id, store_id)
                        VALUES
                            (:zone_id, :code, :tenant_id, :store_id)
                        """
                    ),
                    {
                        "zone_id": str(zone_id),
                        "code": code,
                        "tenant_id": str(tenant_id),
                        "store_id": str(store_id),
                    },
                )

            # One flat rate per migrated zone.
            rate_config = {"amount_cents": max(0, rate_cents)}
            conn.execute(
                sa.text(
                    """
                    INSERT INTO public.shipping_rates
                        (id, tenant_id, zone_id, rate_type, label,
                         config, is_active, sort_order)
                    VALUES
                        (:id, :tenant_id, :zone_id, 'flat', :label,
                         :config, TRUE, 0)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": str(tenant_id),
                    "zone_id": str(zone_id),
                    "label": "Standard",
                    "config": json.dumps(rate_config),
                },
            )
            zones_created += 1

        # Move the legacy zones under a `_legacy_zones` key so we can
        # roll back if needed, and write a migration report the UI can
        # surface as a banner.
        new_shipping = dict(shipping)
        new_shipping["_legacy_zones"] = legacy_zones
        new_shipping.pop("zones", None)
        new_shipping["legacy_migration_report"] = {
            "zones_created": zones_created,
            "unresolved": all_unresolved,
            "migration_revision": revision,
        }
        new_settings = dict(settings)
        new_settings["shipping"] = new_shipping

        conn.execute(
            sa.text("UPDATE public.stores SET settings = :s WHERE id = :id"),
            {"s": json.dumps(new_settings), "id": str(store_id)},
        )


def downgrade() -> None:
    """Restore legacy shipping.zones and wipe the new structured rows.

    This is a best-effort rollback. Any zone/rate rows created by
    merchants *after* this migration (through the new API) will be
    deleted — the downgrade is intended only for emergency revert
    shortly after upgrade.
    """
    conn = op.get_bind()

    # Put `_legacy_zones` back under `zones`.
    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = jsonb_set(
                settings,
                '{shipping,zones}',
                settings -> 'shipping' -> '_legacy_zones',
                true
            )
            WHERE settings -> 'shipping' -> '_legacy_zones' IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = settings #- '{shipping,_legacy_zones}'
                                   #- '{shipping,legacy_migration_report}'
            """
        )
    )

    # Wipe structured rows (cascade via FK).
    conn.execute(sa.text("DELETE FROM public.shipping_rates"))
    conn.execute(sa.text("DELETE FROM public.shipping_zone_governorates"))
    conn.execute(sa.text("DELETE FROM public.shipping_zones"))
