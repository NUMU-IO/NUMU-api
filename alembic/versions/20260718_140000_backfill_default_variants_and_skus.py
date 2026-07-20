"""Backfill: every product gets a sellable unit that agrees with it.

Three reconciliation passes (all idempotent, all reported):

1. Products with NO variant rows (e.g. CSV imports) → create the default
   variant carrying the product's price/quantity/SKU.
2. Simple products (exactly one variant, empty option_values) whose
   variant drifted from the product row (the historical F4 gap: main-form
   stock/price edits never reached the variant) → variant.quantity/price
   := product's values; product.sku copied down when the variant has none.
   The PRODUCT row is treated as truth for historical rows because it is
   what the merchant saw and what checkout debited before unification.
3. Products with no SKU anywhere (product NULL + default variant NULL) →
   generate the standard stable code (SKU-XXXXXXXX, Crockford base32),
   set it on both rows.

SKU writes always respect uq_variants_store_sku: conflicting copies are
skipped and counted, never forced.

Revision ID: backfill_variants_skus_20260718
"""

import uuid as uuid_mod

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "backfill_variants_skus_20260718"
down_revision = "normalize_bridge_prices_20260718"
branch_labels = None
depends_on = None

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _short_code(n: int = 8) -> str:
    value = uuid_mod.uuid4().int
    chars = []
    for _ in range(n):
        value, rem = divmod(value, 32)
        chars.append(_ALPHABET[rem])
    return "".join(chars)


def _sku_taken(bind, store_id, sku) -> bool:
    hit = bind.execute(
        sa.text(
            "SELECT 1 FROM product_variants WHERE store_id = :sid AND sku = :sku "
            "UNION ALL "
            "SELECT 1 FROM products WHERE store_id = :sid AND sku = :sku LIMIT 1"
        ),
        {"sid": store_id, "sku": sku},
    ).first()
    return hit is not None


def upgrade() -> None:
    bind = op.get_bind()
    created = synced = sku_generated = sku_conflicts = 0

    # ── Pass 1: products with no variant rows ──
    orphans = bind.execute(
        sa.text(
            "SELECT p.id, p.tenant_id, p.store_id, p.price_amount, "
            "p.price_currency, p.quantity, p.sku FROM products p "
            "WHERE NOT EXISTS (SELECT 1 FROM product_variants v "
            "WHERE v.product_id = p.id)"
        )
    ).fetchall()
    for pid, tenant_id, store_id, price_amount, currency, quantity, sku in orphans:
        safe_sku = sku
        if safe_sku and _sku_taken(bind, store_id, safe_sku):
            # The product row itself holds this sku, so _sku_taken always
            # matches it — only treat OTHER holders as conflicts.
            other = bind.execute(
                sa.text(
                    "SELECT 1 FROM product_variants WHERE store_id = :sid "
                    "AND sku = :sku LIMIT 1"
                ),
                {"sid": store_id, "sku": safe_sku},
            ).first()
            if other is not None:
                sku_conflicts += 1
                safe_sku = None
        bind.execute(
            sa.text(
                "INSERT INTO product_variants (id, tenant_id, store_id, "
                "product_id, position, option_values, price_amount, "
                "price_currency, sku, inventory_quantity, created_at, "
                "updated_at) VALUES (:id, :tid, :sid, :pid, 0, '{}'::jsonb, "
                ":price, :cur, :sku, :qty, now(), now())"
            ),
            {
                "id": str(uuid_mod.uuid4()),
                "tid": tenant_id,
                "sid": store_id,
                "pid": pid,
                "price": price_amount or 0,
                "cur": currency or "EGP",
                "sku": safe_sku,
                "qty": max(0, quantity or 0),
            },
        )
        created += 1

    # ── Pass 2: simple products whose single default variant drifted ──
    drifted = bind.execute(
        sa.text(
            "SELECT p.id, p.store_id, p.quantity, p.price_amount, "
            "p.price_currency, p.sku, v.id, v.inventory_quantity, "
            "v.price_amount, v.sku "
            "FROM products p JOIN product_variants v ON v.product_id = p.id "
            "WHERE v.option_values = '{}'::jsonb "
            "AND (SELECT count(*) FROM product_variants v2 "
            "     WHERE v2.product_id = p.id) = 1 "
            "AND (v.inventory_quantity != p.quantity "
            "     OR v.price_amount != p.price_amount "
            "     OR (v.sku IS NULL AND p.sku IS NOT NULL))"
        )
    ).fetchall()
    for (
        _pid,
        store_id,
        p_qty,
        p_price,
        p_cur,
        p_sku,
        vid,
        _v_qty,
        _v_price,
        v_sku,
    ) in drifted:
        new_sku = v_sku
        if v_sku is None and p_sku:
            other = bind.execute(
                sa.text(
                    "SELECT 1 FROM product_variants WHERE store_id = :sid "
                    "AND sku = :sku AND id != :vid LIMIT 1"
                ),
                {"sid": store_id, "sku": p_sku, "vid": vid},
            ).first()
            if other is None:
                new_sku = p_sku
            else:
                sku_conflicts += 1
        bind.execute(
            sa.text(
                "UPDATE product_variants SET inventory_quantity = :qty, "
                "price_amount = :price, price_currency = :cur, sku = :sku, "
                "updated_at = now() WHERE id = :vid"
            ),
            {
                "qty": max(0, p_qty or 0),
                "price": p_price or 0,
                "cur": p_cur or "EGP",
                "sku": new_sku,
                "vid": vid,
            },
        )
        synced += 1

    # ── Pass 3: no SKU anywhere → generate ──
    skuless = bind.execute(
        sa.text(
            "SELECT p.id, p.store_id, v.id FROM products p "
            "JOIN product_variants v ON v.product_id = p.id "
            "WHERE p.sku IS NULL AND v.sku IS NULL "
            "AND v.option_values = '{}'::jsonb "
            "AND (SELECT count(*) FROM product_variants v2 "
            "     WHERE v2.product_id = p.id) = 1"
        )
    ).fetchall()
    for pid, store_id, vid in skuless:
        code = f"SKU-{_short_code()}"
        tries = 0
        while _sku_taken(bind, store_id, code) and tries < 5:
            code = f"SKU-{_short_code()}"
            tries += 1
        bind.execute(
            sa.text("UPDATE products SET sku = :sku WHERE id = :pid"),
            {"sku": code, "pid": pid},
        )
        bind.execute(
            sa.text(
                "UPDATE product_variants SET sku = :sku, updated_at = now() "
                "WHERE id = :vid"
            ),
            {"sku": code, "vid": vid},
        )
        sku_generated += 1

    print(
        f"[backfill_default_variants_and_skus] default_variants_created={created} "
        f"simple_synced={synced} skus_generated={sku_generated} "
        f"sku_conflicts_skipped={sku_conflicts}"
    )


def downgrade() -> None:
    # Data reconciliation; generated SKUs are live identifiers once printed
    # on labels/feeds. Intentional no-op.
    pass
