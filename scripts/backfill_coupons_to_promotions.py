"""One-time backfill: wrap every existing active coupon in a Promotion row.

Per the offers-v2 plan (step 14 §10): existing `coupons` rows are NOT
migrated automatically by the schema migration in step 01. Instead this
script creates a matching `promotions` row with
`surface=discount_code` for each active coupon that doesn't already
have a promotion linked.

Idempotent — safe to re-run. Each loop checks whether the coupon
already appears as `promotions.coupon_id` and skips if so.

Usage::

    # Dry-run first to preview changes:
    python scripts/backfill_coupons_to_promotions.py --dry-run

    # Then for real:
    python scripts/backfill_coupons_to_promotions.py

    # Optional: scope to a single store:
    python scripts/backfill_coupons_to_promotions.py --store-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# Add repo root to path so `from src.…` works when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pre-import membership / role models so SQLAlchemy can resolve the
# string-based relationship references on UserModel before any query
# triggers mapper configuration. The package `__init__` doesn't
# currently import these (separate cleanup), and the rest of the app
# only loads them via the FastAPI dependency graph at startup.
from sqlalchemy import select, text  # noqa: E402

import src.infrastructure.database.models.public.membership_override  # noqa: E402, F401
import src.infrastructure.database.models.public.permission  # noqa: E402, F401
import src.infrastructure.database.models.public.role  # noqa: E402, F401
import src.infrastructure.database.models.public.tenant_membership  # noqa: E402, F401
from src.infrastructure.database import AsyncSessionLocal, engine  # noqa: E402
from src.infrastructure.database.models import (  # noqa: E402
    CouponModel,
    PromotionModel,
)


async def _backfill(*, dry_run: bool, store_id: str | None) -> dict:
    """Return a summary dict of what we did / would have done."""
    summary = {
        "examined": 0,
        "skipped_already_linked": 0,
        "skipped_inactive": 0,
        "created": 0,
    }

    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.rls_bypass','true',true)"))

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT set_config('app.rls_bypass','true',true)"))

        coupon_q = select(CouponModel)
        if store_id:
            coupon_q = coupon_q.where(CouponModel.store_id == store_id)
        coupons = (await session.execute(coupon_q)).scalars().all()
        summary["examined"] = len(coupons)

        # Build the set of coupon_ids that are already wrapped in a promotion.
        linked_q = select(PromotionModel.coupon_id).where(
            PromotionModel.coupon_id.is_not(None)
        )
        linked_ids = {
            row for row in (await session.execute(linked_q)).scalars().all() if row
        }

        for coupon in coupons:
            if coupon.id in linked_ids:
                summary["skipped_already_linked"] += 1
                continue
            if not coupon.is_active:
                # Inactive coupons stay legacy-only — backfilling them as
                # archived promotions would clutter the merchant dashboard
                # for no benefit.
                summary["skipped_inactive"] += 1
                continue

            promo = PromotionModel(
                id=uuid4(),
                tenant_id=coupon.tenant_id,
                store_id=coupon.store_id,
                name=f"Code: {coupon.code}",
                surface="discount_code",
                # Active coupons map to active promotions so they keep working
                # without merchant intervention. Scheduled / paused states
                # don't apply here — coupons that were time-bounded keep
                # their `valid_from` / `valid_until` and the storefront
                # resolver honors that via the linked Coupon entity.
                status="active",
                coupon_id=coupon.id,
                discount_rule=None,
                content={"surface": "discount_code"},
                priority=0,
                # Mirror the coupon's window so the merchant list shows the
                # same dates. NULL on either side means "open-ended".
                starts_at=coupon.valid_from,
                ends_at=coupon.valid_until,
                version=1,
            )

            if dry_run:
                print(
                    f"  [dry-run] would create promotion for coupon "
                    f"{coupon.code} ({coupon.id})"
                )
            else:
                session.add(promo)
            summary["created"] += 1

        if not dry_run:
            await session.commit()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created, but don't write to the DB.",
    )
    parser.add_argument(
        "--store-id",
        type=str,
        default=None,
        help="Restrict the backfill to a single store_id (UUID).",
    )
    args = parser.parse_args()

    print(
        "Backfilling coupons -> promotions"
        + (" (dry-run)" if args.dry_run else "")
        + (f" for store={args.store_id}" if args.store_id else " (all stores)"),
    )
    summary = asyncio.run(
        _backfill(dry_run=args.dry_run, store_id=args.store_id),
    )
    print(
        f"Done. examined={summary['examined']} created={summary['created']} "
        f"skipped_already_linked={summary['skipped_already_linked']} "
        f"skipped_inactive={summary['skipped_inactive']}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
