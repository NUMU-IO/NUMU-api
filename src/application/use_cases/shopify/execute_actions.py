"""Execute resolved automation actions against Shopify Admin API.

Takes the output of ``automation_engine.evaluate_rules()`` and dispatches
each action to the appropriate Shopify mutation, logging results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.application.use_cases.shopify.automation_engine import ResolvedAction
from src.infrastructure.external_services.shopify.admin_client import (
    add_tags,
    append_note,
    cancel_order,
    order_gid,
)

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result of executing a single action."""

    action_type: str
    success: bool
    detail: str = ""


async def execute_actions(
    *,
    actions: list[ResolvedAction],
    shop_domain: str,
    access_token: str,
    shopify_order_id: str,
    risk_score: int = 0,
    risk_level: str = "low",
    score_type: str = "preliminary",
    store_id: str = "",
    order_number: str = "",
    total_cents: int = 0,
    currency: str = "EGP",
    customer_phone: str = "",
    customer_name: str = "",
) -> list[ActionResult]:
    """Execute a list of resolved actions against Shopify.

    Parameters
    ----------
    actions:
        Output from ``automation_engine.evaluate_rules()``.
    shop_domain:
        Shopify store domain (e.g. ``"store.myshopify.com"``).
    access_token:
        Decrypted Shopify Admin API access token.
    shopify_order_id:
        Numeric Shopify order ID.
    risk_score:
        Current risk score (for note text).
    risk_level:
        Current risk level (for tag).
    score_type:
        ``"preliminary"`` or ``"final"``.
    store_id:
        UUID string of the store (for payment link creation).
    order_number:
        Shopify order number (for WhatsApp message).
    total_cents:
        Order total in cents (for payment link amount).
    currency:
        Currency code (for payment link).
    customer_phone:
        Customer phone number (for WhatsApp dispatch).
    customer_name:
        Customer name (for WhatsApp message).

    Returns
    -------
    list[ActionResult]
        One result per action attempted.
    """
    if not actions or not access_token:
        return []

    gid = order_gid(shopify_order_id)
    results: list[ActionResult] = []

    for action in actions:
        result = await _dispatch_action(
            action=action,
            shop_domain=shop_domain,
            access_token=access_token,
            shopify_order_id=shopify_order_id,
            gid=gid,
            risk_score=risk_score,
            risk_level=risk_level,
            score_type=score_type,
            store_id=store_id,
            order_number=order_number,
            total_cents=total_cents,
            currency=currency,
            customer_phone=customer_phone,
            customer_name=customer_name,
        )
        results.append(result)

    return results


async def _dispatch_action(
    *,
    action: ResolvedAction,
    shop_domain: str,
    access_token: str,
    shopify_order_id: str,
    gid: str,
    risk_score: int,
    risk_level: str,
    score_type: str,
    store_id: str = "",
    order_number: str = "",
    total_cents: int = 0,
    currency: str = "EGP",
    customer_phone: str = "",
    customer_name: str = "",
) -> ActionResult:
    """Dispatch a single action to the Shopify API."""
    t = action.action_type

    if t == "add_tag":
        tag = action.params.get("tag", action.params.get("value", "custom"))
        ok = await add_tags(shop_domain, access_token, gid, [tag])
        return ActionResult(t, ok, f"tag={tag}")

    if t == "hold_order":
        reason = action.params.get("reason", "risk_hold")
        tags = ["numu-hold", f"numu-risk-{risk_level}"]
        ok_tag = await add_tags(shop_domain, access_token, gid, tags)
        note = f"Order held for review — risk score {risk_score} ({score_type}). Reason: {reason}"
        ok_note = await append_note(shop_domain, access_token, gid, note)
        return ActionResult(t, ok_tag and ok_note, f"reason={reason}")

    if t == "auto_approve":
        ok = await add_tags(shop_domain, access_token, gid, ["numu-approved"])
        return ActionResult(t, ok, "auto_approved")

    if t == "cancel_order":
        # Double-check: cancel ONLY on final score (defense in depth —
        # the automation engine already suppresses this, but belt-and-suspenders)
        if score_type != "final":
            logger.warning(
                "cancel_order blocked at execution layer: score_type=%s", score_type
            )
            return ActionResult(t, False, "blocked: score_type is not final")

        note = action.params.get("reason", f"Auto-cancelled — risk score {risk_score}")
        ok_cancel = await cancel_order(
            shop_domain, access_token, shopify_order_id, reason="fraud", note=note
        )
        if ok_cancel:
            await add_tags(
                shop_domain,
                access_token,
                gid,
                ["numu-cancelled", f"numu-risk-{risk_level}"],
            )
        return ActionResult(t, ok_cancel, f"score={risk_score}")

    if t == "whatsapp_confirm":
        if not store_id or not total_cents:
            logger.warning("whatsapp_confirm missing store_id or total_cents")
            return ActionResult(t, False, "missing_context")

        try:
            from src.infrastructure.messaging.tasks.whatsapp_nudge_task import (
                send_whatsapp_nudge,
            )

            send_whatsapp_nudge.delay(
                store_id=store_id,
                shopify_order_id=shopify_order_id,
                amount_cents=total_cents,
                currency=currency,
                customer_phone=customer_phone,
                customer_name=customer_name,
                order_number=order_number,
            )
            logger.info(
                "WhatsApp nudge enqueued for order %s (store %s)",
                shopify_order_id,
                store_id,
            )
            return ActionResult(t, True, "celery_task_enqueued")
        except Exception as exc:
            logger.error("Failed to enqueue WhatsApp nudge: %s", exc)
            return ActionResult(t, False, f"enqueue_failed: {exc}")

    if t == "add_note":
        note_text = action.params.get("text", action.params.get("note", ""))
        ok = await append_note(shop_domain, access_token, gid, note_text)
        return ActionResult(t, ok, f"note_length={len(note_text)}")

    if t == "send_notification":
        # Generic notification — placeholder
        logger.info(
            "send_notification action for order %s (not implemented)", shopify_order_id
        )
        return ActionResult(t, True, "placeholder")

    logger.warning("Unknown action type: %s", t)
    return ActionResult(t, False, "unknown_action_type")
