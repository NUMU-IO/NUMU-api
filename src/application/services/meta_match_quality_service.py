"""Wave 3 Phase 19 — Meta Event Match Quality (EMQ) dashboard service.

Surfaces Meta's per-event EMQ scores inside the NUMU merchant hub so
merchants don't have to leave for Meta Events Manager to see how well
their PII matches Meta's audience graph.

**Source of truth: Meta's Dataset Quality API** (``GET /{ver}/dataset_quality``
with ``dataset_id``). Corrected 2026-08-17 — this module previously named
``pixels/{id}/event_quality``, an edge that does not answer, and parsed a
``{"data": [...]}`` envelope the real endpoint never returns (it returns
``{"web": [...]}``). Nothing surfaced the mistake because the caller was never
wired up, so the request was never actually made.

Beyond ``composite_score`` the endpoint returns ``match_key_feedback``
(per-identifier coverage), ``diagnostics`` (Meta naming the problem *and* the
solution), ``event_coverage`` (the browser-vs-CAPI gap, measured rather than
inferred) and ``data_freshness``. ``MatchQualitySnapshot`` carries all of it.

**Token requirement.** A long-lived *system user* token with ``ads_read`` plus
``ads_management`` or ``business_management``. A store's CAPI access token
often lacks those scopes, in which case Meta 400s and ``poll_match_quality``
logs Meta's own message and returns empty — the merchant is not told "no data"
when the truth is "wrong scope".

**Wiring.** ``tasks.meta_match_quality_poll`` (Celery beat, every 6 hours)
calls ``poll_match_quality`` for each store that fired an event recently and
writes the results to ``meta_match_quality_snapshot`` via
``MetaMatchQualityRepository``. ``get_snapshots`` reads that table; the hub's
match-quality card and ``GET /admin/tracking/meta/overview`` read the service.
Nothing calls Meta inline — the Marketing API rate-limits per app, and a
dashboard must not fail because a third party is slow.

Snapshots are append-only history on purpose: Meta scores over a rolling
window, so proving a tracking change helped means comparing the same event
across polls, which a single "current" row would erase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MatchQualitySnapshot:
    """One row from the EMQ snapshot table — what the dashboard renders.

    Shape follows Meta's **Dataset Quality API** response, not the older
    ``event_quality`` edge this module used to name. Beyond the headline
    score, that endpoint returns two things worth persisting verbatim:

    * ``match_key_coverage`` — per-identifier coverage, which is what tells a
      merchant *why* the score is what it is (e.g. ip/ua/fbp/external_id/fbc at
      100% with no ``em``/``ph`` is a hard ceiling around 6, no matter what
      else changes).
    * ``diagnostics`` — Meta naming the problem AND stating the solution. That
      copy is better than anything we would write, so it is stored and
      rendered verbatim rather than mapped to our own advice.
    """

    pixel_id: str
    event_name: str
    emq_score: float  # 0.0-10.0 — Meta's `composite_score`
    dedup_rate: float  # 0.00-1.00
    total_events: int
    captured_at: datetime
    # {"fbp": 63.6, "em": 0.0, …} — percentage per match key.
    match_key_coverage: dict[str, float] = field(default_factory=dict)
    # Meta's own [{name, description, solution, percentage, …}] list.
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    # 7-day average % of Pixel events also covered by CAPI. This is the
    # browser-vs-server gap, measured by Meta instead of inferred by us.
    event_coverage: float | None = None
    data_freshness: str | None = None


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
        session: Any = None,
    ) -> list[MatchQualitySnapshot]:
        """Latest EMQ snapshot per event for a store + pixel.

        Reads the cached ``meta_match_quality_snapshot`` rows written by the
        ``meta_match_quality_poll`` beat task — never calls Meta inline, both
        because the Marketing API rate-limits aggressively per app and because
        a dashboard should not fail when a third party is slow.

        An empty list means "no poll has landed yet", which the hub renders
        distinctly from "this store has no Meta connection".

        The old ``oauth_connected`` gate is gone. It short-circuited to ``[]``
        before touching any storage, which is how this service spent its whole
        life returning nothing: the flag was only ever passed as its default
        ``False``. Whether a token can reach the Dataset Quality API is
        answered by the poll task getting a 200, not by a boolean guessed at
        read time.
        """
        if session is None:
            return []

        from src.infrastructure.repositories.meta_match_quality_repository import (
            MetaMatchQualityRepository,
        )

        try:
            return await MetaMatchQualityRepository(session).latest_per_event(
                store_id, pixel_id
            )
        except Exception:  # noqa: BLE001 — a dashboard read must never 500
            logger.warning(
                "match_quality_read_failed", extra={"store_id": str(store_id)}
            )
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

    **Endpoint (verified against Meta's docs 2026-08-17).** This is the
    *Dataset Quality API*, a top-level edge — not ``{pixel_id}/event_quality``,
    which this function used to call and which does not answer::

        GET https://graph.facebook.com/{ver}/dataset_quality
            ?dataset_id={pixel_id}
            &access_token={token}
            &fields=web{event_name,
                        event_match_quality{composite_score,
                                            match_key_feedback{identifier,coverage},
                                            diagnostics{name,description,solution,
                                                        percentage,
                                                        affected_event_count,
                                                        total_event_count}},
                        event_coverage,
                        dedup_key_feedback,
                        data_freshness}

    The response is ``{"web": [ … ]}``, not ``{"data": [ … ]}``.

    **Token.** Needs a long-lived *system user* token with ``ads_read`` plus
    one of ``ads_management`` / ``business_management``. A store's CAPI token
    frequently does NOT carry those scopes — the call then 400s and we log and
    return empty rather than pretending the store has no data.
    """
    import httpx

    from src.config import settings

    api_version = settings.meta_graph_api_version
    url = f"https://graph.facebook.com/{api_version}/dataset_quality"
    fields = (
        "web{event_name,"
        "event_match_quality{composite_score,"
        "match_key_feedback{identifier,coverage},"
        "diagnostics{name,description,solution,percentage,"
        "affected_event_count,total_event_count}},"
        "event_coverage,dedup_key_feedback,data_freshness}"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params={
                    "dataset_id": pixel_id,
                    "access_token": access_token,
                    "fields": fields,
                },
            )
        if resp.status_code >= 400:
            logger.warning(
                "meta_dataset_quality_failed",
                extra={
                    "store_id": str(store_id),
                    "pixel_id": pixel_id,
                    "status": resp.status_code,
                    # Meta's message names the real problem — usually a
                    # missing ads_read/ads_management scope on this token.
                    "body": resp.text[:300],
                },
            )
            return []
        payload = resp.json()
        return [_row_to_snapshot(pixel_id, item) for item in (payload.get("web") or [])]
    except Exception as exc:  # noqa: BLE001 — beat task must continue
        logger.warning(
            "meta_dataset_quality_exception",
            extra={"store_id": str(store_id), "error": str(exc)},
        )
        return []


def _coerce_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


def _row_to_snapshot(pixel_id: str, item: dict[str, Any]) -> MatchQualitySnapshot:
    """Convert one Dataset Quality API ``web[]`` row into our dataclass."""
    emq = item.get("event_match_quality") or {}

    coverage: dict[str, float] = {}
    for entry in emq.get("match_key_feedback") or []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("identifier")
        pct = _coerce_float((entry.get("coverage") or {}).get("percentage"))
        if identifier and pct is not None:
            coverage[str(identifier)] = pct

    diagnostics = [d for d in (emq.get("diagnostics") or []) if isinstance(d, dict)]

    freshness = item.get("data_freshness")
    if isinstance(freshness, dict):
        freshness = freshness.get("upload_frequency")

    dedup = item.get("dedup_key_feedback")
    dedup_rate = _coerce_float(
        dedup.get("percentage") if isinstance(dedup, dict) else dedup
    )

    return MatchQualitySnapshot(
        pixel_id=pixel_id,
        event_name=str(item.get("event_name", "")),
        emq_score=_coerce_float(emq.get("composite_score")) or 0.0,
        dedup_rate=dedup_rate or 0.0,
        total_events=int(item.get("total_event_count") or 0),
        captured_at=datetime.now(UTC),
        match_key_coverage=coverage,
        diagnostics=diagnostics,
        event_coverage=_coerce_float(item.get("event_coverage")),
        data_freshness=str(freshness) if freshness else None,
    )
