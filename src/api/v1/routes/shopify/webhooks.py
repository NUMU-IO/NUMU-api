"""Shopify webhook ingestion endpoint."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.shopify import (
    get_automation_repo,
    get_payment_transaction_repo,
    get_risk_assessment_repo,
    get_shopify_installation_repo,
    get_shopify_settings_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import WebhookProcessRequest
from src.application.use_cases.shopify.risk_scoring_engine import score_order
from src.infrastructure.repositories.shopify_repository import (
    AutomationRepository,
    PaymentTransactionRepository,
    RiskAssessmentRepository,
    ShopifyAppSettingsRepository,
    ShopifyInstallationRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


async def _resolve_store_id(
    shop_domain: str,
    install_repo: ShopifyInstallationRepository,
) -> UUID:
    entity = await install_repo.get_by_domain(shop_domain)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown shop domain: {shop_domain}",
        )
    return entity.id


async def _handle_order_created(
    store_id: UUID,
    payload: dict,
    risk_repo: RiskAssessmentRepository,
    settings_repo: ShopifyAppSettingsRepository,
    automation_repo: AutomationRepository,
) -> dict:
    """Score a new order for risk and apply automation rules."""
    settings = await settings_repo.get_or_create(store_id)

    order_id = str(payload.get("id", ""))
    order_number = str(payload.get("order_number", payload.get("name", "")))
    customer = payload.get("customer") or {}
    total_price = payload.get("total_price", "0")
    currency = payload.get("currency", "EGP")
    payment_method = payload.get(
        "gateway",
        payload.get("payment_gateway_names", ["unknown"])[0]
        if payload.get("payment_gateway_names")
        else "unknown",
    )

    # Convert monetary string to cents
    try:
        total_cents = int(float(total_price) * 100)
    except (ValueError, TypeError):
        total_cents = 0

    # Build order data dict for risk scoring engine
    order_data = {
        "customer_email": customer.get("email", ""),
        "customer_name": f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
        "total_cents": total_cents,
        "currency": currency,
        "payment_method": payment_method,
        "phone": customer.get("phone")
        or (payload.get("shipping_address") or {}).get("phone", ""),
        "shipping_address": payload.get("shipping_address") or {},
        "orders_count": customer.get("orders_count", 0),
        "cancel_rate": 0.0,  # Would need historical lookup
    }

    # Score
    address_parts = order_data["shipping_address"]
    address_str = (
        ", ".join(
            str(address_parts.get(k, ""))
            for k in ("address1", "city", "province", "country")
            if address_parts.get(k)
        )
        or None
    )
    risk_result = score_order(
        total_cents=order_data["total_cents"],
        payment_method=order_data["payment_method"],
        customer_total_orders=order_data.get("orders_count", 0),
        customer_cancellation_rate=order_data.get("cancel_rate"),
        address=address_str,
        phone=order_data.get("phone") or None,
    )

    # Persist risk assessment
    model = await risk_repo.create(
        store_id=store_id,
        order_id=order_id,
        shopify_order_id=order_id,
        order_number=order_number,
        customer_name=order_data["customer_name"],
        customer_email=order_data["customer_email"],
        total_cents=total_cents,
        currency=currency,
        payment_method=payment_method,
        risk_score=risk_result.risk_score,
        risk_level=risk_result.risk_level,
        suggested_action=risk_result.suggested_action,
        factors=[
            {"name": f.factor, "score": f.score, "weight": f.weight, "detail": f.reason}
            for f in risk_result.factors
        ],
    )

    # Auto-apply action based on settings thresholds
    action_taken = None
    if settings.cod_risk_scoring_enabled:
        score = risk_result.risk_score
        if score <= settings.auto_approve_threshold:
            action_taken = "auto_approved"
        elif score >= settings.auto_cancel_threshold:
            action_taken = "auto_cancelled"
        elif score >= settings.auto_hold_threshold:
            action_taken = "held_for_review"

        if action_taken:
            await risk_repo.update_action(model.id, action_taken)

    # Run automation rules
    rules = await automation_repo.list_rules(store_id)
    for rule in rules:
        if not rule.is_active:
            continue
        if rule.trigger_event != "order.created":
            continue

        conditions = rule.conditions or {}
        # Simple condition matching
        match = True
        if (
            "payment_method" in conditions
            and conditions["payment_method"] != payment_method
        ):
            match = False
        if (
            "amount_gte_cents" in conditions
            and total_cents < conditions["amount_gte_cents"]
        ):
            match = False
        if "min_previous_orders" in conditions:
            if order_data.get("orders_count", 0) < conditions["min_previous_orders"]:
                match = False

        if match:
            await automation_repo.create_log(
                store_id=store_id,
                rule_id=rule.id,
                rule_name=rule.name,
                order_id=order_id,
                order_number=order_number,
                trigger_event="order.created",
                actions_executed=rule.actions,
                status="executed",
            )

    return {
        "risk_score": risk_result.risk_score,
        "risk_level": risk_result.risk_level,
        "action_taken": action_taken,
    }


async def _handle_payment_event(
    store_id: UUID,
    topic: str,
    payload: dict,
    pt_repo: PaymentTransactionRepository,
) -> dict:
    """Record a payment success/failure transaction."""
    order = payload.get("order") or payload
    amount_str = payload.get("amount", order.get("total_price", "0"))
    try:
        amount_cents = int(float(amount_str) * 100)
    except (ValueError, TypeError):
        amount_cents = 0

    is_success = "paid" in topic or payload.get("status") == "success"

    await pt_repo.create(
        store_id=store_id,
        order_id=str(order.get("id", "")),
        channel=payload.get("gateway", "unknown"),
        gateway=payload.get("gateway", "unknown"),
        display_name=payload.get("gateway", "Unknown"),
        amount_cents=amount_cents,
        currency=payload.get("currency", order.get("currency", "EGP")),
        status="completed" if is_success else "failed",
        failure_reason=payload.get("error_message"),
        failure_code=payload.get("error_code"),
        gateway_transaction_id=str(payload.get("id", "")),
    )

    return {"recorded": True, "status": "completed" if is_success else "failed"}


@router.post(
    "/process",
    response_model=SuccessResponse[dict],
    summary="Process incoming Shopify webhook",
    operation_id="shopify_process_webhook",
)
async def process_webhook(
    body: WebhookProcessRequest,
    install_repo: Annotated[
        ShopifyInstallationRepository, Depends(get_shopify_installation_repo)
    ],
    risk_repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    pt_repo: Annotated[
        PaymentTransactionRepository, Depends(get_payment_transaction_repo)
    ],
    automation_repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
    settings_repo: Annotated[
        ShopifyAppSettingsRepository, Depends(get_shopify_settings_repo)
    ],
):
    store_id = await _resolve_store_id(body.shop_domain, install_repo)
    topic = body.topic

    logger.info("Shopify webhook received: topic=%s shop=%s", topic, body.shop_domain)

    result: dict = {}

    if topic in ("orders/create", "orders/updated"):
        result = await _handle_order_created(
            store_id=store_id,
            payload=body.payload,
            risk_repo=risk_repo,
            settings_repo=settings_repo,
            automation_repo=automation_repo,
        )
    elif topic in ("orders/paid", "orders/partially_paid", "payment_failed"):
        result = await _handle_payment_event(
            store_id=store_id,
            topic=topic,
            payload=body.payload,
            pt_repo=pt_repo,
        )
    elif topic == "app/uninstalled":
        await install_repo.mark_uninstalled(body.shop_domain)
        result = {"uninstalled": True}
    elif topic == "shop/redact":
        # GDPR: Merchant uninstalled 48 h ago — delete ALL store data
        counts = await install_repo.delete_store_data(store_id)
        logger.info("GDPR shop/redact for %s: deleted %s", body.shop_domain, counts)
        result = {"redacted": True, "deleted": counts}
    elif topic == "customers/redact":
        # GDPR: Delete all data associated with a specific customer
        customer = body.payload.get("customer", {})
        email = customer.get("email", "")
        if email:
            deleted = await risk_repo.delete_by_customer_email(store_id, email)
            logger.info(
                "GDPR customers/redact for %s (%s): deleted %d records",
                body.shop_domain,
                email,
                deleted,
            )
            result = {"redacted": True, "records_deleted": deleted}
        else:
            result = {
                "redacted": True,
                "records_deleted": 0,
                "note": "no email in payload",
            }
    elif topic == "customers/data_request":
        # GDPR: Report what data we hold for a specific customer
        customer = body.payload.get("customer", {})
        email = customer.get("email", "")
        records = (
            await risk_repo.list_by_customer_email(store_id, email) if email else []
        )
        result = {
            "customer_email": email,
            "data_held": [
                {
                    "type": "risk_assessment",
                    "order_number": r.order_number,
                    "risk_score": r.risk_score,
                    "created_at": str(r.created_at),
                }
                for r in records
            ],
        }
    else:
        logger.warning("Unhandled webhook topic: %s", topic)
        result = {"acknowledged": True, "topic": topic}

    return SuccessResponse(data=result, message=f"Webhook '{topic}' processed")
