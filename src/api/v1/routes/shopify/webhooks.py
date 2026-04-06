"""Shopify webhook ingestion endpoint.

Shopify HMAC-SHA256 verification is handled by the Remix frontend app
(``authenticate.webhook(request)``).  The backend is protected by the
``X-Internal-Key`` header (constant-time comparison in
``verify_internal_key``).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies.shopify import (
    get_automation_repo,
    get_network_reputation_repo,
    get_payment_transaction_repo,
    get_risk_assessment_repo,
    get_shopify_installation_repo,
    get_shopify_settings_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import WebhookProcessRequest
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_payload as _extract_phone_hash,
)
from src.application.services.network_reputation_service import (
    lookup_network_reputation,
)
from src.application.services.network_reputation_service import (
    write_network_event as _write_network_event,
)
from src.application.use_cases.shopify.automation_engine import (
    OrderContext,
    evaluate_rules,
)
from src.application.use_cases.shopify.execute_actions import execute_actions
from src.application.use_cases.shopify.risk_scoring_engine import (
    score_order_fast,
)
from src.infrastructure.messaging.tasks.risk_scoring_tasks import (
    compute_full_risk_score,
)
from src.infrastructure.repositories.shopify_repository import (
    AutomationRepository,
    NetworkReputationRepository,
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


_STORE_AVG_KEY = "shopify:store:{store_id}:avg_order_cents"
_STORE_AVG_TTL = 30 * 24 * 3600  # 30 days
_STORE_AVG_ALPHA = 0.10  # EMA weight for the new observation
_STORE_AVG_DEFAULT = 80_000  # 800 EGP fallback


async def _get_and_update_store_avg(store_id: UUID, total_cents: int) -> int:
    """Read the store's rolling average order value from Redis and update it.

    Uses an Exponential Moving Average (EMA) so recent orders matter more.
    Falls back to the hardcoded default if Redis is unavailable.
    Returns the *previous* average (used for the fast score of this order).

    Circuit-breaker: if Redis read fails, return the fallback but do NOT
    update the EMA — this prevents a stale fallback value from poisoning
    the average when Redis comes back up.
    """
    try:
        from src.infrastructure.cache.redis_cache import RedisCacheService

        cache = RedisCacheService()
        key = _STORE_AVG_KEY.format(store_id=store_id)
        cached = await cache.get(key)

        if cached is None:
            # First order or key expired — seed with this order's value
            await cache.set(key, total_cents, expire=_STORE_AVG_TTL)
            await cache.close()
            return _STORE_AVG_DEFAULT

        prev_avg = int(cached)
        new_avg = int(
            prev_avg * (1 - _STORE_AVG_ALPHA) + total_cents * _STORE_AVG_ALPHA
        )
        await cache.set(key, new_avg, expire=_STORE_AVG_TTL)
        await cache.close()
        return prev_avg
    except Exception as exc:
        # Circuit-breaker: return fallback WITHOUT writing to Redis.
        # This avoids poisoning the EMA with _STORE_AVG_DEFAULT when
        # Redis recovers — the real EMA value is preserved on disk.
        logger.warning("Redis store-avg lookup failed (store %s): %s", store_id, exc)
        return _STORE_AVG_DEFAULT


# Network reputation helpers (lookup, hash, write) live in
# src.application.services.network_reputation_service. Imported above as
# `_lookup_network_score`-equivalent (`lookup_network_reputation`),
# `_extract_phone_hash`, and `_write_network_event` for API compatibility.


async def _lookup_network_score(
    phone_hash: str | None,
    network_repo: NetworkReputationRepository,
) -> tuple[int, str]:
    """Backwards-compatible shim returning ``(score, label)``.

    The new shared service returns ``(score, confidence, label)``; this
    helper drops the confidence so existing Shopify callers don't need to
    change. New callers should use ``lookup_network_reputation`` directly.
    """
    score, _confidence, label = await lookup_network_reputation(
        phone_hash, network_repo
    )
    return score, label


async def _handle_order_created(
    store_id: UUID,
    payload: dict,
    risk_repo: RiskAssessmentRepository,
    settings_repo: ShopifyAppSettingsRepository,
    automation_repo: AutomationRepository,
    network_repo: NetworkReputationRepository,
    install_repo: ShopifyInstallationRepository,
) -> dict:
    """Score a new order for risk and apply automation rules.

    Scoring is split into two layers:
    - **Sync fast score** (2 factors, <200ms): ``network_reputation`` baseline
      + ``order_value`` vs store Redis average.  Persisted immediately as
      ``score_type="preliminary"``.
    - **Async full score** (5 factors, <10s): Celery task ``compute_full_risk_score``
      upgrades the record to ``score_type="final"`` once complete.
    """
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

    shipping_address = payload.get("shipping_address") or {}
    phone = customer.get("phone") or shipping_address.get("phone", "")
    customer_name = (
        f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
    )
    customer_email = customer.get("email", "")
    orders_count = customer.get("orders_count", 0)

    address_str = (
        ", ".join(
            str(shipping_address.get(k, ""))
            for k in ("address1", "city", "province", "country")
            if shipping_address.get(k)
        )
        or None
    )

    # ── 1. Network reputation lookup + write ────────────────────────────────
    phone_hash = _extract_phone_hash(payload)

    # Read BEFORE write so the current order doesn't inflate the score
    network_score, network_label = await _lookup_network_score(phone_hash, network_repo)

    # Write the "order" event to network reputation
    await _write_network_event(
        phone_hash=phone_hash,
        store_id=store_id,
        event_type="order",
        network_repo=network_repo,
    )

    # ── 2. Fast score (synchronous, <200ms) ─────────────────────────────────
    # Get store rolling average BEFORE updating it so this order is scored
    # against the *previous* average.
    avg_order_cents = await _get_and_update_store_avg(store_id, total_cents)

    fast_result = score_order_fast(
        total_cents=total_cents,
        avg_order_cents=avg_order_cents,
        network_score=network_score,
        network_label=network_label,
    )

    # ── 3. Persist preliminary assessment ───────────────────────────────────
    model = await risk_repo.create(
        store_id=store_id,
        order_id=order_id,
        shopify_order_id=order_id,
        order_number=order_number,
        customer_name=customer_name,
        customer_email=customer_email,
        total_cents=total_cents,
        currency=currency,
        payment_method=payment_method,
        risk_score=fast_result.risk_score,
        risk_level=fast_result.risk_level,
        suggested_action=fast_result.suggested_action,
        score_type="preliminary",
        factors=[
            {"name": f.factor, "score": f.score, "weight": f.weight, "detail": f.reason}
            for f in fast_result.factors
        ],
    )

    # ── 4. Enqueue full 5-factor Celery task (async, <10s) ──────────────────
    try:
        compute_full_risk_score.delay(
            assessment_id=str(model.id),
            store_id=str(store_id),
            total_cents=total_cents,
            payment_method=payment_method,
            customer_total_orders=orders_count,
            customer_cancellation_rate=None,  # Phase 3: enrich from store history
            address=address_str,
            phone=phone or None,
            avg_order_cents=avg_order_cents,
        )
    except Exception as exc:
        # Celery unavailable → preliminary score stays operative, never crash
        logger.error(
            "Failed to enqueue full risk score task for assessment %s: %s",
            model.id,
            exc,
        )

    # ── 5. Apply threshold-based auto-action on the preliminary score ────────
    # SAFETY: auto-cancel must ONLY fire on score_type="final" (Option A).
    # Auto-approve and auto-hold are safe on preliminary because they are
    # reversible.
    action_taken = None
    if settings.cod_risk_scoring_enabled:
        score = fast_result.risk_score
        if score <= settings.auto_approve_threshold:
            action_taken = "auto_approved"
        elif score >= settings.auto_cancel_threshold:
            # DO NOT auto-cancel on preliminary — defer to Celery final score
            action_taken = None
        elif score >= settings.auto_hold_threshold:
            action_taken = "held_for_review"

        if action_taken:
            await risk_repo.update_action(model.id, action_taken)

    # ── 6. Run automation rules (order.created trigger) ─────────────────────
    installation = await install_repo.get_by_store_id(store_id)

    rules = await automation_repo.list_rules(store_id)
    ctx = OrderContext(
        risk_score=fast_result.risk_score,
        risk_level=fast_result.risk_level,
        score_type="preliminary",
        payment_method=payment_method,
        total_cents=total_cents,
        customer_total_orders=orders_count,
        installed_at=installation.installed_at if installation else None,
    )
    resolved_actions = evaluate_rules(rules, ctx, trigger_event="order.created")

    if resolved_actions and installation:
        action_results = await execute_actions(
            actions=resolved_actions,
            shop_domain=installation.shopify_domain,
            access_token=installation.access_token_encrypted,
            shopify_order_id=order_id,
            risk_score=fast_result.risk_score,
            risk_level=fast_result.risk_level,
            score_type="preliminary",
            store_id=str(store_id),
            order_number=order_number,
            total_cents=total_cents,
            currency=currency,
            customer_phone=phone,
            customer_name=customer_name,
        )
        # Log each triggered rule
        logged_rule_ids = set()
        for ra in resolved_actions:
            if ra.source_rule_id not in logged_rule_ids:
                logged_rule_ids.add(ra.source_rule_id)
                rule_actions = [
                    a for a in resolved_actions if a.source_rule_id == ra.source_rule_id
                ]
                rule_results = [
                    r
                    for r, a in zip(action_results, resolved_actions)
                    if a.source_rule_id == ra.source_rule_id
                ]
                all_ok = all(r.success for r in rule_results)
                any_ok = any(r.success for r in rule_results)
                log_status = (
                    "success" if all_ok else "partial_failure" if any_ok else "failed"
                )
                await automation_repo.create_log(
                    store_id=store_id,
                    rule_id=ra.source_rule_id,
                    rule_name=ra.source_rule_name,
                    order_id=order_id,
                    order_number=order_number,
                    trigger_event="order.created",
                    actions_executed=[
                        {"type": a.action_type, **a.params} for a in rule_actions
                    ],
                    status=log_status,
                )
        # Bump times_triggered on matched rules
        for ra in resolved_actions:
            await automation_repo.increment_triggered(ra.source_rule_id)
    elif resolved_actions:
        # Rules matched but no installation — log without executing
        logger.warning(
            "Automation rules matched but no active installation for store %s",
            store_id,
        )

    return {
        "risk_score": fast_result.risk_score,
        "risk_level": fast_result.risk_level,
        "score_type": "preliminary",
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
    network_repo: Annotated[
        NetworkReputationRepository, Depends(get_network_reputation_repo)
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
            network_repo=network_repo,
            install_repo=install_repo,
        )
    elif topic in ("orders/cancelled", "orders/fulfilled", "refunds/create"):
        # Network reputation write path — record cancellation/delivery/refund
        phone_hash = _extract_phone_hash(body.payload)
        event_map = {
            "orders/cancelled": "rto",
            "orders/fulfilled": "delivery",
            "refunds/create": "refund",
        }
        event_type = event_map[topic]
        await _write_network_event(
            phone_hash=phone_hash,
            store_id=store_id,
            event_type=event_type,
            network_repo=network_repo,
        )
        result = {"acknowledged": True, "topic": topic, "event_recorded": event_type}
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
    elif topic == "app/scopes_update":
        # Mandatory Shopify webhook — update stored scopes
        installation = await install_repo.get_by_domain(body.shop_domain)
        if installation:
            installation.scopes = body.payload.get("scopes", [])
        result = {"scopes_updated": True}
    elif topic == "shop/redact":
        # GDPR: Merchant uninstalled 48 h ago — delete ALL store data
        # Network contribution rollback is handled inside delete_store_data
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
