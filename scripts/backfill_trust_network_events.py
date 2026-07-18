#!/usr/bin/env python3
"""One-time backfill: replay NUMU's ``network_contribution_log`` into the standalone
Trust Network's ``POST /v1/events`` (P1-7.5).

NUMU's ``phone_hash`` IS the Trust Network's token once its ``K_net`` equals NUMU's
``PLATFORM_SECRET_SALT`` (byte-identical tokenization — see the parity test), so this is
a near-verbatim copy of the ledger: it bootstraps the whole ~14k-customer graph in one
pass. Every other partner then feeds the same endpoint.

Idempotent — safe to re-run. Each row is posted with its ``dedup_key`` as the
Idempotency-Key (or ``numu:<row-id>`` synthesized for the legacy null-key rows), so the
Trust Network's unique ``dedup_key`` makes a replay a no-op. **Strict consent:** rows for
stores that set ``trust_network_enabled = false`` are skipped (stores with no settings
row default to consented).

Recommended flow (avoids double-counting with the live feed): enable the ongoing feed
first, then run this with ``--before <that time>`` so history and new outcomes never
overlap.

Usage::

    # Preview (no posts):
    python scripts/backfill_trust_network_events.py --dry-run

    # Real, only rows before the feed went live:
    python scripts/backfill_trust_network_events.py --before 2026-07-17T12:00:00Z

    # Scope / tune:
    python scripts/backfill_trust_network_events.py --store-id <uuid> --concurrency 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add repo root to path so `from src.…` works when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

# Pre-import membership/role models so SQLAlchemy resolves string-based relationships
# before the first query triggers mapper configuration (mirrors the coupon backfill).
import src.infrastructure.database.models.public.membership_override  # noqa: E402, F401
import src.infrastructure.database.models.public.permission  # noqa: E402, F401
import src.infrastructure.database.models.public.role  # noqa: E402, F401
import src.infrastructure.database.models.public.tenant_membership  # noqa: E402, F401
from src.application.services.trust_network_feed import (  # noqa: E402
    feed_config,
    post_outcome,
)
from src.infrastructure.database import AsyncSessionLocal, engine  # noqa: E402
from src.infrastructure.database.models import (  # noqa: E402
    NetworkContributionLogModel,
    ShopifyAppSettingsModel,
)


async def _opted_out_store_ids(session: Any) -> set[str]:
    """Stores that explicitly opted out. Stores with no settings row default to
    consented (``trust_network_enabled`` server-defaults to true)."""
    rows = await session.execute(
        select(ShopifyAppSettingsModel.store_id).where(
            ShopifyAppSettingsModel.trust_network_enabled.is_(False)
        )
    )
    return {str(s) for s in rows.scalars().all()}


async def _backfill(
    *,
    dry_run: bool,
    before: datetime | None,
    batch_size: int,
    concurrency: int,
    limit: int,
    store_id: str | None,
) -> dict[str, int]:
    cfg = feed_config()
    url, api_key = str(cfg["url"]), str(cfg["api_key"])
    if not dry_run and not (url and api_key):
        raise SystemExit(
            "TRUST_NETWORK_URL and TRUST_NETWORK_API_KEY must be set to backfill."
        )

    summary = {"examined": 0, "skipped_opted_out": 0, "sent": 0, "failed": 0}
    sem = asyncio.Semaphore(concurrency)

    # RLS bypass for a cross-store admin read (matches the coupon backfill precedent).
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.rls_bypass','true',true)"))

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT set_config('app.rls_bypass','true',true)"))
        opted_out = await _opted_out_store_ids(session)

        async with httpx.AsyncClient(timeout=float(cfg["timeout"])) as client:  # type: ignore[arg-type]

            async def _send(row: NetworkContributionLogModel) -> bool:
                async with sem:
                    dedup_key = row.dedup_key or f"numu:{row.id}"
                    return await post_outcome(
                        client,
                        url=url,
                        api_key=api_key,
                        phone_hash=row.phone_hash,
                        event_type=row.event_type,
                        dedup_key=dedup_key,
                    )

            last_id = None
            while True:
                q = (
                    select(NetworkContributionLogModel)
                    .order_by(NetworkContributionLogModel.id)
                    .limit(batch_size)
                )
                if last_id is not None:
                    q = q.where(NetworkContributionLogModel.id > last_id)
                if before is not None:
                    q = q.where(NetworkContributionLogModel.created_at < before)
                if store_id is not None:
                    q = q.where(NetworkContributionLogModel.store_id == store_id)

                rows = list((await session.execute(q)).scalars().all())
                if not rows:
                    break
                last_id = rows[-1].id
                summary["examined"] += len(rows)

                todo = [r for r in rows if str(r.store_id) not in opted_out]
                summary["skipped_opted_out"] += len(rows) - len(todo)

                if dry_run:
                    summary["sent"] += len(todo)
                else:
                    results = await asyncio.gather(*[_send(r) for r in todo])
                    summary["sent"] += sum(1 for ok in results if ok)
                    summary["failed"] += sum(1 for ok in results if not ok)

                print(
                    f"  … {summary['examined']} examined, {summary['sent']} sent",
                    flush=True,
                )
                if limit and summary["examined"] >= limit:
                    break

    return summary


def main() -> None:
    p = argparse.ArgumentParser(
        description="Backfill network_contribution_log → Trust Network /v1/events"
    )
    p.add_argument("--dry-run", action="store_true", help="count only, post nothing")
    p.add_argument(
        "--before",
        type=str,
        default=None,
        help="ISO8601; only rows created strictly before this (set to the feed-enable "
        "time so history can't overlap the live feed)",
    )
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument(
        "--limit", type=int, default=0, help="stop after ~N examined (testing)"
    )
    p.add_argument("--store-id", type=str, default=None, help="scope to one store")
    args = p.parse_args()

    before = (
        datetime.fromisoformat(args.before.replace("Z", "+00:00"))
        if args.before
        else None
    )
    summary = asyncio.run(
        _backfill(
            dry_run=args.dry_run,
            before=before,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            limit=args.limit,
            store_id=args.store_id,
        )
    )
    print(f"backfill {'(dry-run) ' if args.dry_run else ''}done: {summary}")


if __name__ == "__main__":
    main()
