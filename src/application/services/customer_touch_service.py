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

    # Look at the most recent prior touch for dedup + the
    # is_first_touch flag.
    #
    # Scope selection — what counts as "prior" depends on identity:
    # * authenticated visitors (customer_id known): use
    #   (store_id, customer_id) so a returning customer on a new
    #   device doesn't get a second "first touch" — the flag is
    #   per-customer-ever, not per-session.
    # * anonymous visitors (customer_id absent): fall back to
    #   (store_id, session_fingerprint). Sessions get backfilled at
    #   checkout via ``backfill_session_touches`` which then
    #   re-evaluates is_first_touch across the now-known customer's
    #   full history.
    if customer_id is not None:
        prior_query = (
            select(CustomerTouchModel)
            .where(
                CustomerTouchModel.store_id == store_id,
                CustomerTouchModel.customer_id == customer_id,
            )
            .order_by(desc(CustomerTouchModel.ts))
            .limit(1)
        )
    else:
        prior_query = (
            select(CustomerTouchModel)
            .where(
                CustomerTouchModel.store_id == store_id,
                CustomerTouchModel.session_fingerprint == session_fingerprint,
            )
            .order_by(desc(CustomerTouchModel.ts))
            .limit(1)
        )
    result = await session.execute(prior_query)
    prior = result.scalar_one_or_none()

    if prior is not None and _utms_equal(
        prior,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    ):
        # Same touch identity as the immediately-prior row — refresh
        # or retry, not a new touch.
        return None

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
        is_first_touch=(prior is None),
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
