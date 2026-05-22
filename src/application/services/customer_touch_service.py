"""Customer touch capture service.

Called from the `/track` endpoint whenever a funnel event arrives
with UTM data. Decides whether to append a new ``customer_touches``
row or skip (if the previous touch on this session has identical
UTMs).

Three rules at the trust boundary:
1. **UTMs required**: A touch with no UTM data, no gclid/fbclid, and
   no referrer is not a "touch" — it's internal navigation. Skip.
2. **Dedup against previous touch**: Same session refreshing a
   UTM-tagged page should not duplicate the row. We compare the
   immediately-prior touch's normalized utm_source/medium/campaign.
3. **Customer_id stays NULL until conversion**: Even when the
   request comes from an authenticated customer, we still write
   ``customer_id`` so the journey reads cleanly. For anonymous
   sessions it stays NULL and is backfilled at checkout via
   :py:func:`backfill_session_touches`.

The dedup query reads the most recent row by (session_fingerprint,
ts DESC LIMIT 1). The (session_fingerprint, ts) index makes this an
index scan — bounded by session length, not table size.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.customer_touch import (
    CustomerTouchModel,
)


def _has_attribution_signal(
    *,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
    gclid: str | None,
    fbclid: str | None,
    referrer: str | None,
) -> bool:
    """Decide whether a request carries enough to count as a touch.

    A bare page_view with no UTMs / referrer / click-id is internal
    navigation — recording it would dilute the journey timeline with
    noise. We require at least one UTM dimension, click-id, or an
    external referrer to count as a "touch".
    """
    return any(
        v and v.strip()
        for v in (utm_source, utm_medium, utm_campaign, gclid, fbclid, referrer)
    )


def _utms_equal(
    row: CustomerTouchModel,
    *,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
) -> bool:
    """Whether a stored touch row has identical UTM identity to a new one.

    Only source/medium/campaign matter for dedup — term/content are
    free-form (creative IDs, search keywords) and shouldn't dedup a
    second touch. gclid/fbclid are not used for dedup either; the
    same ad click landing twice still counts as one touch.

    Normalization is lower+trim so ``Facebook`` and ``facebook`` and
    ``  Facebook `` collapse to the same touch.
    """

    def _n(v: str | None) -> str:
        return (v or "").strip().lower()

    return (
        _n(row.utm_source) == _n(utm_source)
        and _n(row.utm_medium) == _n(utm_medium)
        and _n(row.utm_campaign) == _n(utm_campaign)
    )


async def maybe_capture_touch(
    *,
    session: AsyncSession,
    store_id: UUID,
    tenant_id: UUID,
    session_fingerprint: str,
    customer_id: UUID | None,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
    utm_term: str | None,
    utm_content: str | None,
    gclid: str | None,
    fbclid: str | None,
    referrer: str | None,
    landing_path: str | None,
    campaign_id: UUID | None,
    ts: datetime | None = None,
) -> CustomerTouchModel | None:
    """Append a customer_touches row when this event represents a real
    new touch; skip otherwise.

    Returns the created row (or ``None`` when skipped). Caller owns
    the surrounding transaction commit.

    Edge cases handled:
    * No attribution signal at all → skipped (internal navigation).
    * Identical UTMs to the previous touch on this session → skipped
      (refresh / retry).
    * First touch on a brand-new session → ``is_first_touch=True``.
    """
    if not session_fingerprint:
        # Without a fingerprint we have nothing to bind the touch to.
        # A track event without a fingerprint is broken upstream
        # (storefront didn't generate one); silently skip rather than
        # surface a 500 to the visitor.
        return None

    if not _has_attribution_signal(
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        gclid=gclid,
        fbclid=fbclid,
        referrer=referrer,
    ):
        return None

    # Dedup check — ALWAYS scoped to the same session.
    #
    # A page refresh / sendBeacon retry on a UTM-tagged URL would
    # otherwise duplicate the row. But dedup must NOT span sessions:
    # a returning customer who clicks the SAME campaign link in a
    # fresh session is a legitimate new touch, even though the UTMs
    # match a touch from a previous session. Scoping dedup to
    # session_fingerprint preserves that distinction.
    dedup_query = (
        select(CustomerTouchModel)
        .where(
            CustomerTouchModel.store_id == store_id,
            CustomerTouchModel.session_fingerprint == session_fingerprint,
        )
        .order_by(desc(CustomerTouchModel.ts))
        .limit(1)
    )
    prior_in_session = (await session.execute(dedup_query)).scalar_one_or_none()

    if prior_in_session is not None and _utms_equal(
        prior_in_session,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    ):
        # Same touch identity as the immediately-prior row IN THIS
        # SESSION — refresh or retry, not a new touch.
        return None

    # is_first_touch lookup — broader scope when the customer is
    # known.
    #
    # If there's already a prior row in THIS session, we already know
    # this isn't the first touch — no extra query needed.
    # Otherwise: for authenticated visitors (customer_id present),
    # look across all of the customer's touches; for anonymous
    # visitors, the session-scoped check we already did is the best
    # signal we have. Backfill re-evaluates the flag once the
    # customer is identified at checkout, so guesting-then-converting
    # still ends up with exactly one is_first_touch=True row per
    # customer.
    if prior_in_session is not None:
        is_first_touch = False
    elif customer_id is not None:
        customer_prior = await session.execute(
            select(CustomerTouchModel.id)
            .where(
                CustomerTouchModel.store_id == store_id,
                CustomerTouchModel.customer_id == customer_id,
            )
            .limit(1)
        )
        is_first_touch = customer_prior.scalar_one_or_none() is None
    else:
        is_first_touch = True

    touch = CustomerTouchModel(
        store_id=store_id,
        tenant_id=tenant_id,
        customer_id=customer_id,
        session_fingerprint=session_fingerprint,
        ts=ts or datetime.now(UTC),
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        utm_term=utm_term,
        utm_content=utm_content,
        gclid=gclid,
        fbclid=fbclid,
        referrer=referrer,
        landing_path=landing_path,
        campaign_id=campaign_id,
        is_first_touch=is_first_touch,
    )
    session.add(touch)
    await session.flush()
    return touch


async def backfill_session_touches(
    *,
    session: AsyncSession,
    store_id: UUID,
    session_fingerprint: str,
    customer_id: UUID,
) -> int:
    """Link anonymous touches to a now-known customer.

    Called from the checkout path when a guest converts. Updates every
    prior touch for ``(store_id, session_fingerprint)`` that doesn't
    yet have a customer_id — typically the entire pre-auth browsing
    history.

    SEC: the ``store_id`` filter is critical. ``session_fingerprint``
    is generated client-side and is NOT cryptographically
    unforgeable; two visitors on two different stores could in
    principle collide on fingerprint. Without the store_id scope,
    backfilling at checkout on store A would silently link store B's
    anonymous touches to this customer — breaking journey isolation
    between merchants on the same NUMU instance.

    Returns the number of rows updated. Single-statement UPDATE
    avoids reading-then-writing under concurrent /track inserts on
    the same session.
    """
    if not session_fingerprint:
        return 0
    result = await session.execute(
        update(CustomerTouchModel)
        .where(
            CustomerTouchModel.store_id == store_id,
            CustomerTouchModel.session_fingerprint == session_fingerprint,
            CustomerTouchModel.customer_id.is_(None),
        )
        .values(customer_id=customer_id)
    )
    linked = result.rowcount or 0

    # Re-evaluate `is_first_touch` across the customer's full
    # touch history. At capture time the flag is set per-session
    # for anonymous visits — a guest browsing across two devices
    # could end up with TWO `is_first_touch=True` rows pre-backfill.
    # Once we know the customer_id, mark only the earliest touch
    # as the first touch and clear the flag on every other touch.
    # Done in a single UPDATE with a scalar subquery so the math
    # stays atomic under concurrent /track inserts.
    if linked > 0:
        earliest = (
            select(CustomerTouchModel.id)
            .where(
                CustomerTouchModel.store_id == store_id,
                CustomerTouchModel.customer_id == customer_id,
            )
            .order_by(CustomerTouchModel.ts.asc())
            .limit(1)
            .scalar_subquery()
        )
        await session.execute(
            update(CustomerTouchModel)
            .where(
                CustomerTouchModel.store_id == store_id,
                CustomerTouchModel.customer_id == customer_id,
            )
            .values(is_first_touch=(CustomerTouchModel.id == earliest))
        )

    return linked
