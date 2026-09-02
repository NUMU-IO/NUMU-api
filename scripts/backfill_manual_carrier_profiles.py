"""Backfill a courier profile for stores already using manual shipping.

``shipping.manual.enabled`` ships **True on every store**, including both
live ones, so every merchant already has manual shipping switched on
without ever having defined a courier. Without a profile they would open
the new UI to an empty list and lose a working setup.

**This does not change behaviour, it represents it.** The profile written
is derived from a flag that is already ``true``; nothing a merchant sees
starts or stops working. That is why the risk is lower than "a script
that writes to production" sounds.

Dry run by default. It prints exactly which stores would be written and
what the row would contain, and touches nothing until ``--apply`` is
passed.

    # See what would happen — writes nothing
    .venv/Scripts/python.exe scripts/backfill_manual_carrier_profiles.py

    # Only the two live stores, still a dry run
    .venv/Scripts/python.exe scripts/backfill_manual_carrier_profiles.py \\
        --store vionne --store rabbit

    # Actually write
    .venv/Scripts/python.exe scripts/backfill_manual_carrier_profiles.py --apply

Safe to re-run: a store that already has a profile is skipped.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.services.manual_carrier_profiles import (  # noqa: E402
    backfill_default_profile,
    list_profiles,
)


async def _load_stores(slugs: list[str] | None):
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with AsyncSessionLocal() as session:
        repo = StoreRepository(session)
        stores = await repo.list_all() if hasattr(repo, "list_all") else []
        if slugs:
            wanted = {s.lower() for s in slugs}
            stores = [
                s
                for s in stores
                if (getattr(s, "slug", "") or "").lower() in wanted
                or (getattr(s, "name", "") or "").lower() in wanted
            ]
        return stores


async def run(slugs: list[str] | None, apply: bool) -> int:
    stores = await _load_stores(slugs)
    if not stores:
        print("No stores matched. Nothing to do.")
        return 0

    would_write: list[tuple[str, dict]] = []
    skipped_disabled: list[str] = []
    skipped_existing: list[str] = []

    for store in stores:
        label = getattr(store, "slug", None) or str(store.id)
        settings = store.settings or {}
        manual = settings.get("shipping", {}).get("manual", {})

        if not manual.get("enabled"):
            skipped_disabled.append(label)
            continue
        if list_profiles(settings):
            skipped_existing.append(label)
            continue

        updated = backfill_default_profile(settings)
        if updated is None:
            skipped_existing.append(label)
            continue

        profile = list_profiles(updated)[0]
        would_write.append((label, profile.to_dict()))

        if apply:
            from src.infrastructure.database.connection import AsyncSessionLocal
            from src.infrastructure.repositories.store_repository import (
                StoreRepository,
            )

            async with AsyncSessionLocal() as session:
                repo = StoreRepository(session)
                fresh = await repo.get_by_id(store.id)
                # Re-read and re-check: another process may have created a
                # profile since the scan above.
                if fresh and not list_profiles(fresh.settings or {}):
                    fresh.settings = backfill_default_profile(fresh.settings or {})
                    if fresh.settings is not None:
                        await repo.update(fresh)
                        await session.commit()

    print(f"\nStores scanned:            {len(stores)}")
    print(f"Already have a profile:    {len(skipped_existing)}")
    print(f"Manual shipping disabled:  {len(skipped_disabled)}")
    print(f"Would write a profile to:  {len(would_write)}")

    for label, profile in would_write:
        print(f"\n  store: {label}")
        print(f"    name_en:  {profile['name_en']}")
        print(f"    name_ar:  {profile['name_ar']}")
        print(
            f"    coverage: {'everywhere' if not profile['governorate_codes'] else profile['governorate_codes']}"
        )
        print(f"    active:   {profile['is_active']}")

    if apply:
        print("\nAPPLIED. Re-run without --apply to confirm nothing is left.")
    else:
        print("\nDRY RUN — nothing was written. Pass --apply to write.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        action="append",
        dest="stores",
        help="Limit to a store slug. Repeatable. Default: every store.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without this the script only reports.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.stores, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
