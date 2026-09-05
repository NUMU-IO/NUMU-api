"""Backfill a courier profile for stores already using manual shipping.

``_get_default_shipping_settings()`` returns ``manual.enabled = True``,
so a merchant sees manual shipping switched on without ever having
defined a courier. Without a profile they would open the new UI to an
empty list and lose a working setup.

**That default applies on read, not in storage.** Measured against a real
database: most stores have no persisted ``shipping`` block at all, and
only those that stored the flag are backfilled here. Writing a profile
for a store that never configured shipping would be *creating* state
rather than representing it, and it will get one the first time it
touches courier settings anyway.

**For the stores this does touch, it does not change behaviour — it
represents it.** The profile is derived from a flag already stored as
``true``; nothing a merchant sees starts or stops working.

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

# Courier names are Arabic and a Windows console defaults to cp1252, which
# raises on them. Without this the script dies while *printing its report*
# — and on an --apply run that happens after stores have been written,
# leaving an operator with a traceback instead of a result.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from src.application.services.manual_carrier_profiles import (  # noqa: E402
    backfill_default_profile,
    list_profiles,
)

#: `get_all` paginates. A silent first-page-only scan would report
#: "nothing to do" for every store past the first hundred.
_PAGE = 200


async def _load_stores(slugs: list[str] | None):
    """Every store, paginated.

    The repository method is ``get_all``, not ``list_all``. An earlier
    version of this script guarded with ``hasattr(repo, "list_all")`` and
    so always fell through to an empty list — it printed "No stores
    matched. Nothing to do." and exited 0. A migration that reports
    success while touching nothing is the worst possible failure here, so
    the method is called directly and an empty result is treated as a
    finding rather than a normal outcome.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.store_repository import StoreRepository

    stores: list = []
    async with AsyncSessionLocal() as session:
        repo = StoreRepository(session)
        skip = 0
        while True:
            page = await repo.get_all(skip=skip, limit=_PAGE)
            stores.extend(page)
            if len(page) < _PAGE:
                break
            skip += _PAGE

    if not stores:
        raise RuntimeError(
            "The database returned no stores at all. That is almost "
            "certainly a connection or permissions problem rather than an "
            "empty platform — refusing to report success."
        )

    if slugs:
        wanted = {s.lower() for s in slugs}
        matched = [
            s
            for s in stores
            if (getattr(s, "slug", "") or "").lower() in wanted
            or (getattr(s, "name", "") or "").lower() in wanted
        ]
        missing = (
            wanted
            - {(getattr(s, "slug", "") or "").lower() for s in matched}
            - {(getattr(s, "name", "") or "").lower() for s in matched}
        )
        if missing:
            # Silently scanning zero stores because a slug was mistyped is
            # how a migration "succeeds" without running.
            raise RuntimeError(
                f"No store matched: {', '.join(sorted(missing))}. "
                f"Check the slug — refusing to continue on a partial match."
            )
        return matched

    return stores


async def run(slugs: list[str] | None, apply: bool) -> int:
    stores = await _load_stores(slugs)
    if not stores:
        print("No stores matched. Nothing to do.")
        return 0

    would_write: list[tuple[str, dict]] = []
    skipped_disabled: list[str] = []
    skipped_unconfigured: list[str] = []
    skipped_existing: list[str] = []

    for store in stores:
        label = getattr(store, "slug", None) or str(store.id)
        settings = store.settings or {}
        shipping = settings.get("shipping") or {}
        manual = shipping.get("manual") or {}

        # Most stores have never stored a shipping block at all. They see
        # manual shipping as on because the *read path* defaults it, but
        # nothing is persisted. Writing a profile for them would be
        # creating state rather than representing it, so they are left
        # alone — they get a profile the first time they touch courier
        # settings. Only stores that actually stored the flag are
        # backfilled.
        if "manual" not in shipping:
            skipped_unconfigured.append(label)
            continue

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

    print(f"\nStores scanned:               {len(stores)}")
    print(f"Never configured shipping:    {len(skipped_unconfigured)}")
    print(f"Manual shipping switched off: {len(skipped_disabled)}")
    print(f"Already have a profile:       {len(skipped_existing)}")
    print(f"Would write a profile to:     {len(would_write)}")

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
