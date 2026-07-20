"""Normalize bridge-created variant prices stored 100x too high.

The legacy-attributes bridge (`_options_variants_from_legacy_attributes`)
pre-multiplied combo prices by 100 before handing them to
`_materialize_product_variants`, which itself treats the value as MAJOR
units (`Money(amount=...)`) and persists `.cents` — so bridge-created
variants stored price_amount 100x too high (300 EGP combo -> 3,000,000
cents). Every other variant write path (default variant, variants CRUD
route) uses the correct majors convention.

This migration finds variants of products that carry
`attributes.variant_combinations`, matches each combo to its variant by
option_values, and divides price_amount by 100 ONLY where it equals the
exact x100-inflated value — a conservative, idempotent match. Rows whose
price matches neither the correct nor the inflated expectation are left
untouched and counted in the report.

Also merges the two open heads (mtv_updated_at_20260715,
platform_benchmarks_20260715).

Revision ID: normalize_bridge_prices_20260718
"""

from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "normalize_bridge_prices_20260718"
down_revision = ("mtv_updated_at_20260715", "platform_benchmarks_20260715")
branch_labels = None
depends_on = None


def _norm(d):
    return {str(k).lower(): str(v).lower() for k, v in (d or {}).items()}


def upgrade() -> None:
    bind = op.get_bind()

    products = bind.execute(
        sa.text(
            "SELECT id, attributes FROM products "
            "WHERE jsonb_typeof(attributes->'variant_combinations') = 'array'"
        )
    ).fetchall()

    fixed = 0
    already_ok = 0
    unmatched_price = 0

    for prod_id, attributes in products:
        combos = (attributes or {}).get("variant_combinations") or []
        variants = bind.execute(
            sa.text(
                "SELECT id, option_values, price_amount FROM product_variants "
                "WHERE product_id = :pid"
            ),
            {"pid": prod_id},
        ).fetchall()
        if not variants:
            continue
        by_options = {}
        for vid, option_values, price_amount in variants:
            by_options.setdefault(
                tuple(sorted(_norm(option_values).items())), (vid, price_amount)
            )

        for combo in combos:
            if not isinstance(combo, dict):
                continue
            raw_price = combo.get("price")
            try:
                major = Decimal(str(raw_price))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if major <= 0:
                continue
            key = tuple(sorted(_norm(combo.get("options")).items()))
            match = by_options.get(key)
            if match is None:
                continue
            vid, price_amount = match
            correct_cents = int(major * 100)
            inflated_cents = int(major * 100 * 100)
            if price_amount == inflated_cents:
                bind.execute(
                    sa.text(
                        "UPDATE product_variants SET price_amount = :p WHERE id = :vid"
                    ),
                    {"p": correct_cents, "vid": vid},
                )
                fixed += 1
            elif price_amount == correct_cents:
                already_ok += 1
            else:
                unmatched_price += 1

    print(
        f"[normalize_bridge_variant_prices] products_with_combos={len(products)} "
        f"fixed={fixed} already_ok={already_ok} unmatched={unmatched_price}"
    )


def downgrade() -> None:
    # Irreversible data normalization: after the fix, x100-inflated rows are
    # indistinguishable from legitimately-priced ones. Intentional no-op.
    pass
