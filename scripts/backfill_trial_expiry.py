"""Give existing tenants the trial expiry they were never issued.

`expire_trials` selects on ``lifecycle_state = 'trial' AND expires_at < now()``.
Until now the only path that produced that state was a demo conversion: a
direct signup created its tenant with the column default, ``active``, and no
expiry. Measured on production, every one of 51 tenants was ``active`` with
``expires_at IS NULL`` — so the task has never had a row to find, and no
merchant has ever been asked to subscribe.

This represents the trial each of those merchants was actually given at
signup. The date is the user's ``trial_ends_at`` — the value the merchant was
told at registration and the value the hub will now count down.

**Merchants whose trial has already lapsed are the delicate case.** They have
been selling on an unenforced trial for weeks; flipping them to read-only in
one run would lock live stores with no warning. They are given a fresh window
of ``--grace-days`` (default 7) from today instead, so the countdown appears,
the banner escalates, and the lock lands on a date they can see coming. Pass
``--grace-days 0`` to lock them on the next `expire_trials` tick.

Never touched:

* tenants on a paid plan, ``payg``, or ``beta`` — they converted, and a trial
  expiry on a paying merchant is a lock waiting to happen;
* ``demo`` and ``read_only`` tenants — the demo-cleanup and purge tasks own
  those lifecycles;
* any tenant that already has an ``expires_at``.

Dry run by default; prints every row it would write and touches nothing until
``--apply`` is passed.

    venv/Scripts/python.exe scripts/backfill_trial_expiry.py
    venv/Scripts/python.exe scripts/backfill_trial_expiry.py --grace-days 14
    venv/Scripts/python.exe scripts/backfill_trial_expiry.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.public.user import UserModel

# A tenant on one of these has converted, or is not ours to expire.
CONVERTED_PLANS = {"starter", "pro", "enterprise", "payg", "beta"}


async def _plan(grace_days: int) -> list[tuple[TenantModel, datetime, str]]:
    now = datetime.now(UTC)
    rows: list[tuple[TenantModel, datetime, str]] = []

    async with AsyncSessionLocal() as session:
        tenants = (
            (
                await session.execute(
                    select(TenantModel).where(
                        TenantModel.expires_at.is_(None),
                        TenantModel.lifecycle_state == TenantLifecycleState.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )

        owners = {
            user.id: user
            for user in (
                (
                    await session.execute(
                        select(UserModel).where(
                            UserModel.id.in_([
                                t.owner_id for t in tenants if t.owner_id
                            ])
                        )
                    )
                )
                .scalars()
                .all()
            )
        }

        for tenant in tenants:
            if (tenant.plan or "").lower() in CONVERTED_PLANS:
                continue
            owner = owners.get(tenant.owner_id)
            if owner is None or owner.trial_ends_at is None:
                # No trial was ever stamped at signup. Inventing one here would
                # be creating state rather than representing it.
                continue

            ends = owner.trial_ends_at
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=UTC)

            if ends > now:
                rows.append((tenant, ends, "remaining trial"))
            elif grace_days > 0:
                rows.append((
                    tenant,
                    now + timedelta(days=grace_days),
                    "lapsed → grace",
                ))
            else:
                rows.append((tenant, ends, "lapsed → locks on next tick"))

    return rows


async def _apply(rows) -> int:
    written = 0
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        for stale, expires_at, _why in rows:
            tenant = await session.get(TenantModel, stale.id)
            if tenant is None or tenant.expires_at is not None:
                continue  # changed under us — leave it alone
            tenant.lifecycle_state = TenantLifecycleState.TRIAL
            tenant.plan = "trial"
            tenant.expires_at = expires_at
            tenant.trial_started_at = (
                tenant.trial_started_at or tenant.created_at or now
            )
            written += 1
        await session.commit()
    return written


async def _run(apply: bool, grace_days: int) -> None:
    """Plan and write in ONE event loop.

    Two `asyncio.run` calls opened two loops, and the shared engine's pooled
    asyncpg connections are pinned to the loop that opened them — the second
    call died on checkout with "got Future attached to a different loop"
    (`connection.py` documents the same trap for Celery, which sidesteps it
    with NullPool). Nothing was written, because the failure landed before the
    commit; still, a backfill that half-runs is exactly the kind of thing this
    must not be able to do.
    """
    rows = await _plan(grace_days)
    if not rows:
        print("Nothing to backfill.")
        return

    print(f"{len(rows)} tenant(s) would be put on trial:\n")
    for tenant, expires_at, why in rows:
        print(
            f"  {tenant.subdomain:<24} plan={tenant.plan:<8} "
            f"expires={expires_at:%Y-%m-%d}  ({why})"
        )

    if not apply:
        print("\nDry run — nothing written. Pass --apply to write.")
        return

    written = await _apply(rows)
    print(f"\nWrote {written} tenant(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument(
        "--grace-days",
        type=int,
        default=7,
        help="window for merchants whose trial already lapsed (0 = lock next tick)",
    )
    args = parser.parse_args()

    asyncio.run(_run(args.apply, args.grace_days))


if __name__ == "__main__":
    main()
