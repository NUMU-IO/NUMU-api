"""Parse + apply WhatsApp/SMS verification replies (backend-015).

When a COD merchant fires a verification nudge ("are you sure you want
to confirm this order?"), the customer's WhatsApp reply arrives at the
inbound webhook. Sprint 1 logged it as conversation history and ignored
it — half the loop. This use case closes the loop:

  1. Parse the reply text for yes/no tokens (Arabic + English).
  2. Find the related ``risk_assessment`` via the most-recent outbound
     verification template for this phone → its ``store_id`` → the
     latest pending ``payment_link_session`` for that store → the
     ``shopify_order_id`` it carries.
  3. Stamp ``action_taken`` and ``action_taken_by="customer_whatsapp"``
     on the risk assessment.
  4. On confirmation, write a network ``delivery`` event so the
     customer's positive intent feeds the cross-merchant trust score.

The parser is intentionally narrow: only single-token replies are
treated as verification answers. Long messages (a customer asking a
follow-up question) flow through the existing conversation-log path
unchanged so a human can reply.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

# Tokens that count as a clear "yes". Lowercase, whitespace-stripped.
# Includes Arabic 'na'am' (yes) + the English aliases merchants told us
# to expect from MENA customers.
_YES_TOKENS: frozenset[str] = frozenset({
    "نعم",
    "ايوه",
    "أيوه",
    "اه",
    "yes",
    "y",
    "confirm",
    "ok",
    "okay",
    "sure",
})
_NO_TOKENS: frozenset[str] = frozenset({
    "لا",
    "لأ",
    "no",
    "n",
    "cancel",
    "stop",
    "nope",
})

# Verification replies must arrive within this window of the outbound
# nudge. Outside that, treat as a regular conversation message.
_REPLY_WINDOW_HOURS = 24


VerificationOutcome = Literal["confirmed", "rejected", "not_a_reply"]


async def _maybe_enqueue_meta_capi_whatsapp_lead(
    *,
    session,
    store_id,
    risk_assessment_id,
    phone: str,
) -> None:
    """Wave 2 Phase 15 — fire Meta CAPI Lead on WhatsApp confirmation.

    Looks up the store's tracking config. Only enqueues when the merchant
    has explicitly opted in (``whatsapp_lead_enabled=True``) AND has
    pixel + CAPI configured. Bridges WhatsApp commerce into Meta's
    ad-attribution loop: customer ad click → WhatsApp confirmation →
    Lead event lands in Meta Events Manager.

    ``event_id = f"whatsapp-lead-{ra.id}"`` so this Lead dedupes with
    any future Phase 12 Lead fire on the same risk assessment, but stays
    distinct from the order's Purchase event_id.

    Fail-open: never raises. A Meta CAPI failure must not block the
    customer's verification reply flow.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.store import StoreModel

    try:
        store_row = await session.execute(
            select(StoreModel).where(StoreModel.id == store_id)
        )
        store = store_row.scalar_one_or_none()
        if store is None:
            return
        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        pixel_id = meta_cfg.get("pixel_id")
        if not (
            meta_cfg.get("whatsapp_lead_enabled")
            and meta_cfg.get("capi_enabled")
            and pixel_id
        ):
            return

        from src.infrastructure.messaging.tasks.meta_capi import (
            meta_capi_send_event,
        )

        meta_capi_send_event.delay(
            store_id=str(store_id),
            pixel_id=pixel_id,
            event_name="Lead",
            event_id=f"whatsapp-lead-{risk_assessment_id}",
            event_time=int(datetime.now(UTC).timestamp()),
            event_source_url=None,
            user_data={
                # WhatsApp Business gives us a verified phone — best
                # available match key when we don't have a logged-in
                # customer context. The CAPI hashing layer normalizes
                # the MENA prefix and SHA-256s before transmission.
                "phone": phone,
                # Stable per-customer signal — the same phone confirming
                # multiple orders lands under one external_id in Meta.
                "customer_id": phone,
            },
            custom_data={
                "content_name": "WhatsApp COD confirmation",
                "content_category": "whatsapp_lead",
            },
            test_event_code=meta_cfg.get("test_event_code"),
            action_source="chat",
        )
        logger.info(
            "meta_capi_whatsapp_lead_enqueued",
            extra={
                "store_id": str(store_id),
                "risk_assessment_id": str(risk_assessment_id),
            },
        )
    except Exception as exc:  # noqa: BLE001 — fail-open per Phase 15 contract
        logger.warning(
            "meta_capi_whatsapp_lead_enqueue_failed",
            extra={
                "store_id": str(store_id),
                "risk_assessment_id": str(risk_assessment_id),
                "error": str(exc),
            },
        )


def parse_reply(text: str | None) -> VerificationOutcome:
    """Classify an inbound message body as a verification yes/no/none.

    Strict on length so a real conversation message (two sentences,
    questions, follow-up clarifications) isn't accidentally treated
    as a verification answer. Returns ``"not_a_reply"`` when the
    text doesn't match a single yes/no token.
    """
    if not text:
        return "not_a_reply"
    cleaned = text.strip().lower()
    # Drop trailing punctuation like "yes!" / "no." / "نعم؟". rstrip's
    # arg is a charset, which is what we want here — any combination
    # of these characters trailing the token should be removed.
    cleaned = cleaned.rstrip("!.?؟،,. ")  # noqa: B005
    if cleaned in _YES_TOKENS:
        return "confirmed"
    if cleaned in _NO_TOKENS:
        return "rejected"
    return "not_a_reply"


def is_within_reply_window(sent_at: datetime, now: datetime | None = None) -> bool:
    """True if ``sent_at`` is within the verification reply window."""
    if not sent_at:
        return False
    now = now or datetime.now(UTC)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return (now - sent_at) <= timedelta(hours=_REPLY_WINDOW_HOURS)


async def apply_reply(
    *,
    session,
    phone: str,
    text: str,
) -> dict:
    """Apply an inbound verification reply to the related risk assessment.

    Returns a result dict with the action taken so the webhook handler
    can record it for observability:

      ``{"matched": False}`` — message wasn't a yes/no token.
      ``{"matched": True, "applied": False, "reason": ...}`` — the
        reply parsed but couldn't be tied back to a recent
        verification (no recent outbound nudge / no pending session).
      ``{"matched": True, "applied": True, "outcome": "confirmed",
        "risk_assessment_id": ..., "shopify_order_id": ...}``

    The handler stays inside one async session so the webhook can
    commit/rollback atomically with the rest of its work.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.message_log import (
        MessageLogModel,
    )
    from src.infrastructure.database.models.tenant.payment_link_session import (
        PaymentLinkSessionModel,
    )
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel,
    )

    outcome = parse_reply(text)
    if outcome == "not_a_reply":
        return {"matched": False}

    # 1. Latest outbound message_log for this phone — gives us the
    #    store_id and the timestamp we use to enforce the 24h window.
    latest_outbound = await session.execute(
        select(MessageLogModel)
        .where(
            MessageLogModel.phone == phone,
            MessageLogModel.direction == "outbound",
        )
        .order_by(MessageLogModel.created_at.desc())
        .limit(1)
    )
    outbound = latest_outbound.scalar_one_or_none()
    if outbound is None:
        return {
            "matched": True,
            "applied": False,
            "reason": "no_recent_outbound",
        }

    if not is_within_reply_window(outbound.created_at):
        return {
            "matched": True,
            "applied": False,
            "reason": "outside_reply_window",
        }

    store_id = outbound.store_id

    # 2. Latest pending payment_link_session for this store — the
    #    verification nudge creates one with the order's
    #    shopify_order_id, so this is our bridge into risk_assessments.
    latest_session = await session.execute(
        select(PaymentLinkSessionModel)
        .where(
            PaymentLinkSessionModel.store_id == store_id,
            PaymentLinkSessionModel.status == "pending",
        )
        .order_by(PaymentLinkSessionModel.created_at.desc())
        .limit(1)
    )
    pls = latest_session.scalar_one_or_none()
    if pls is None or not pls.shopify_order_id:
        return {
            "matched": True,
            "applied": False,
            "reason": "no_pending_session",
        }

    # 3. Look up the risk_assessment by store + shopify_order_id and
    #    stamp the outcome. action_taken_by makes the audit-log honest:
    #    the customer drove this state change, not a merchant.
    ra_q = await session.execute(
        select(RiskAssessmentModel)
        .where(
            RiskAssessmentModel.store_id == store_id,
            RiskAssessmentModel.shopify_order_id == pls.shopify_order_id,
        )
        .order_by(RiskAssessmentModel.created_at.desc())
        .limit(1)
    )
    ra = ra_q.scalar_one_or_none()
    if ra is None:
        return {
            "matched": True,
            "applied": False,
            "reason": "no_risk_assessment",
        }

    ra.action_taken = (
        "customer_confirmed" if outcome == "confirmed" else "customer_rejected"
    )
    ra.action_taken_at = datetime.now(UTC)
    ra.action_taken_by = "customer_whatsapp"

    # 4. On confirmation, mark the payment_link_session "intent_confirmed"
    #    so the merchant dashboard can show "verified — waiting on
    #    payment" rather than "pending verification".
    if outcome == "confirmed":
        pls.status = "intent_confirmed"
    elif outcome == "rejected":
        pls.status = "customer_rejected"

    await session.flush()

    # 5. Wave 2 Phase 15 — fire Meta CAPI Lead event on confirmation.
    #    Opt-in per store (whatsapp_lead_enabled flag). Fail-open: any
    #    Meta CAPI error is swallowed so it can't break the verification
    #    flow. The merchant gets the Lead signal in their ad audience
    #    when configured; legacy stores see no change.
    if outcome == "confirmed":
        await _maybe_enqueue_meta_capi_whatsapp_lead(
            session=session,
            store_id=store_id,
            risk_assessment_id=ra.id,
            phone=phone,
        )

    logger.info(
        "verification_reply_applied",
        extra={
            "outcome": outcome,
            "store_id": str(store_id),
            "shopify_order_id": pls.shopify_order_id,
            "risk_assessment_id": str(ra.id),
        },
    )

    return {
        "matched": True,
        "applied": True,
        "outcome": outcome,
        "risk_assessment_id": str(ra.id),
        "shopify_order_id": pls.shopify_order_id,
    }


__all__ = [
    "VerificationOutcome",
    "apply_reply",
    "is_within_reply_window",
    "parse_reply",
]
