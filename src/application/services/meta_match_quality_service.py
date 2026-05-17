"""Wave 3 Phase 19 — Meta Event Match Quality (EMQ) dashboard service.

Surfaces Meta's per-event EMQ scores inside the NUMU merchant hub so
merchants don't have to leave for Meta Events Manager to see how well
their PII matches Meta's audience graph.

**Gate (production-readiness):** EMQ data is fetched via Meta's
Marketing API ``pixels/{id}/event_quality`` endpoint, which requires
an OAuth-acquired token with ``ads_management`` scope (Phase 17). Until
OAuth + App Review land, ``get_snapshots`` returns an empty list and
the merchant-hub UI renders a "Connect Meta Business to see scores"
empty state.

Once OAuth is live, the hourly ``poll_match_quality`` Celery beat
task hits Marketing API for every connected store and snapshots the
results into ``meta_match_quality_snapshot`` (Postgres) so the
dashboard reads cached data instead of going to Meta on every page
load (Marketing API has aggressive per-app rate limits).

v1 schema (per the plan):
    meta_match_quality_snapshot(
        store_id UUID,
        pixel_id VARCHAR(32),
        event_name VARCHAR(64),
        emq_score NUMERIC(3,1),       -- 0.0-10.0
        dedup_rate NUMERIC(3,2),      -- 0.00-1.00
        total_events INTEGER,
        captured_at TIMESTAMPTZ
    )

The schema migration is in v1.1 — for now the service queries through
the existing application stack and returns empty results for
unconnected stores. This MVP unblocks the dashboard UI work without
requiring the migration to land first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.config.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MatchQualitySnapshot:
    """One row from the EMQ snapshot table — what the dashboard renders."""

    pixel_id: str
    event_name: str
    emq_score: float  # 0.0-10.0
    dedup_rate: float  # 0.00-1.00
    total_events: int
    captured_at: datetime


@dataclass(frozen=True)
class MatchQualityActionItem:
    """Prescriptive action item the dashboard suggests to improve EMQ."""

    title: str
    body: str
    category: str  # "advanced_matching" | "dedup" | "capi_connection" | "consent"


# Prescriptive action items the dashboard renders when EMQ for a given
# event is below the "good" threshold (Meta's recommendation is ≥6.5).
# Ordered by typical impact — addressing the first item usually moves
# the needle most.
_LOW_EMQ_ACTIONS: tuple[MatchQualityActionItem, ...] = (
    MatchQualityActionItem(
        title="Increase Advanced Matching field coverage",
        body=(
            "Your events are missing phone or email for many customers. "
            "Ensure checkout collects both and that the storefront passes "
            "them through to /track. Aim for ≥80% of events to have at "
            "least 4 of: em, ph, fn, ln, ct, country, zp."
        ),
        category="advanced_matching",
    ),
    MatchQualityActionItem(
        title="Verify dedup contract",
        body=(
            "Less than 75% of Pixel events have a matching server-side CAPI "
            "event with the same event_id. Check the storefront is sending "
            "event_id via fireMetaEvent and the backend is forwarding it "
            "to the meta_capi_send_event task verbatim."
        ),
        category="dedup",
    ),
    MatchQualityActionItem(
        title="Connect CAPI if not already connected",
        body=(
            "Your store is firing browser-side Pixel only. Adding the "
            "Conversions API (server-side fire from your webhooks) "
            "typically lifts EMQ 1-2 points and protects against ad "
            "blockers."
        ),
        category="capi_connection",
    ),
    MatchQualityActionItem(
        title="Raise consent acceptance rate",
        body=(
            "When customers deny marketing consent, NUMU sends Meta's "
            "opt_out parameter so events still count for attribution math "
            "without storing first-party data. Currently >40% of your "
            "events arrive opted-out, which caps how high EMQ can go. "
            "Consider softening your consent banner copy."
        ),
        category="consent",
    ),
)


class MetaMatchQualityService:
    """Reads cached EMQ snapshots + computes prescriptive actions.

    Stateless — instantiate per request. The hourly Celery beat task
    is the writer (when OAuth-connected stores have refreshable data);
    this service only reads.
    """

    LOW_EMQ_THRESHOLD = 6.5  # Meta's recommended floor

    def __init__(self) -> None:
        pass

    async def get_snapshots(
        self,
        store_id: UUID,
        pixel_id: str | None = None,
        oauth_connected: bool = False,
    ) -> list[MatchQualitySnapshot]:
        """Return the latest EMQ snapshot per event for a store + pixel.

        Returns empty list when the store hasn't OAuth-connected
        (the only path EMQ data flows in via). UI renders a
        "Connect Meta Business" empty state in that case.

        When connected but no snapshots yet (just connected, first
        poll hasn't run), also returns empty — UI shows "Waiting
        for first poll, check back in 1 hour".
        """
        if not oauth_connected:
            logger.debug(
                "match_quality_skipped_not_oauth_connected",
                extra={"store_id": str(store_id)},
            )
            return []
        # v1.1: query the meta_match_quality_snapshot table via
        # MetaMatchQualitySnapshotRepository. v1 returns an empty list
        # until the migration + repository land — the merchant-hub UI
        # treats this the same as "no snapshots yet".
        return []

    def actions_for_event(
        self, snapshot: MatchQualitySnapshot
    ) -> list[MatchQualityActionItem]:
        """Return prescriptive actions for events below the threshold.

        When the EMQ score is healthy (≥6.5), returns an empty list —
        UI renders a green checkmark. Below threshold, returns the
        full action list so the merchant can prioritize.
        """
        if snapshot.emq_score >= self.LOW_EMQ_THRESHOLD:
            return []
        return list(_LOW_EMQ_ACTIONS)


async def poll_match_quality(
    *, store_id: UUID, pixel_id: str, access_token: str
) -> list[MatchQualitySnapshot]:
    """Wave 3 Phase 19 — pull EMQ from Meta's Marketing API.

    Called by the hourly Celery beat task ``meta_match_quality_poll``
    for every OAuth-connected store. Snapshots get persisted to
    ``meta_match_quality_snapshot`` so the dashboard reads cached data.

    **v1 — gated behind Phase 17 OAuth.** When called, requires a
    valid access_token with ``ads_management`` scope. Returns an
    empty list (no exception) on transient API failures so the beat
    task continues iterating other stores.

    Marketing API endpoint per Meta docs:
        GET https://graph.facebook.com/{ver}/{pixel_id}/event_quality
            ?access_token={token}
    """
    import httpx

    from src.config import settings

    api_version = getattr(settings, "meta_graph_api_version", "v21.0")
    url = f"https://graph.facebook.com/{api_version}/{pixel_id}/event_quality"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"access_token": access_token})
        if resp.status_code >= 400:
            logger.warning(
                "marketing_api_event_quality_failed",
                extra={
                    "store_id": str(store_id),
                    "pixel_id": pixel_id,
                    "status": resp.status_code,
                    "body": resp.text[:300],
                },
            )
            return []
        payload = resp.json()
        return [
            _row_to_snapshot(pixel_id, item) for item in (payload.get("data") or [])
        ]
    except Exception as exc:  # noqa: BLE001 — beat task must continue
        logger.warning(
            "marketing_api_event_quality_exception",
            extra={"store_id": str(store_id), "error": str(exc)},
        )
        return []


def _row_to_snapshot(pixel_id: str, item: dict[str, Any]) -> MatchQualitySnapshot:
    """Convert one Marketing API event_quality row into our dataclass."""
    return MatchQualitySnapshot(
        pixel_id=pixel_id,
        event_name=str(item.get("event_name", "")),
        emq_score=float(item.get("score", 0.0)),
        dedup_rate=float(item.get("dedup_rate", 0.0)),
        total_events=int(item.get("total_events", 0)),
        captured_at=datetime.now(UTC),
    )
