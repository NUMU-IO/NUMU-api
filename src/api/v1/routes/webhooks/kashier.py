"""Kashier webhook handler.

Receives payment notifications from Kashier for:
- Card payments (success/failure)
- Wallet payments (success/failure)
- Bank installments (success/failure)

Uses per-tenant credential resolution:
1. Parse payload to extract merchantOrderId (untrusted)
2. Look up order to get tenant_id
3. Load tenant's Kashier credentials from DB
4. Verify signature with tenant-specific API key
5. Process payment status change
"""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.tenant.configuration import (
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.kashier import KashierPaymentService
from src.infrastructure.repositories.credential_repository import (
    CredentialRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant

logger = get_logger(__name__)
router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

NONCE_TTL_SECONDS = 86_400  # 24 hours


@router.post("/callback", operation_id="kashier_callback")
async def kashier_callback(
    request: Request,
    db: AsyncSession = Depends(get_admin_db_session),
    x_kashier_signature: str = Header(None, alias="x-kashier-signature"),
):
    """Handle Kashier payment callback with per-tenant credential resolution.

    Kashier sends a POST with payment transaction details.
    The x-kashier-signature header contains the HMAC SHA256 signature.

    Flow:
    1. Parse payload (untrusted) to extract merchantOrderId
    2. Look up order to resolve tenant_id
    3. Load tenant's Kashier API key from encrypted credentials
    4. Verify HMAC signature with the resolved key
    5. Process payment status (SUCCESS/FAILED)
    """
    payload = await request.body()
    log = logger.bind(webhook="kashier")

    # ── Phase A: Parse payload and resolve tenant ────────────────
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        log.warning("webhook_invalid_json")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    merchant_order_id = data.get("merchantOrderId")
    transaction_id = data.get("transactionId")
    payment_status = data.get("paymentStatus")
    kashier_order_id = data.get("orderId")
    amount = data.get("amount")
    currency = data.get("currency", "EGP")

    log = log.bind(
        transaction_id=transaction_id,
        kashier_order_id=kashier_order_id,
        merchant_order_id=merchant_order_id,
        payment_status=payment_status,
        amount=amount,
        currency=currency,
    )
    log.info("webhook_received")

    # ── Replay protection ────────────────────────────────────────
    if transaction_id and _cache_service:
        nonce_key = f"kashier:processed:{transaction_id}"
        was_set = await _cache_service.set_if_absent(
            nonce_key, "1", expire=NONCE_TTL_SECONDS
        )
        if not was_set:
            log.warning("webhook_duplicate_rejected")
            return {"status": "duplicate", "transaction_id": transaction_id}

    # ── Order lookup ─────────────────────────────────────────────
    order_repo = OrderRepository(db)
    order = None
    if merchant_order_id:
        order = await order_repo.get_by_payment_id_for_update(merchant_order_id)

    if not order:
        log.warning("webhook_order_not_found", lookup_id=merchant_order_id)
        return {"status": "received", "transaction_id": transaction_id}

    log = log.bind(order_id=str(order.id), order_number=order.order_number)

    # ── Phase B: Load tenant credentials and verify signature ────
    api_key: str | None = None

    cred_repo = CredentialRepository(db)
    creds = await cred_repo.get_decrypted_credentials(
        tenant_id=order.tenant_id,
        service_type=ServiceType.PAYMENT_GATEWAY,
        service_name=ServiceName.KASHIER,
    )

    if creds:
        api_key = creds.get("api_key")
        log.info("webhook_using_tenant_credentials")
    else:
        api_key = settings.kashier_api_key
        log.info("webhook_using_env_credentials")

    if api_key:
        kashier_service = KashierPaymentService(api_key=api_key)
        verified_data = kashier_service.verify_webhook_signature(
            payload, x_kashier_signature or ""
        )
        if not verified_data:
            log.warning("webhook_signature_invalid")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
    else:
        log.warning("webhook_no_api_key_available", mode="development")

    # ── RLS narrowing (bypass -> tenant-scoped) ──────────────────
    await narrow_to_tenant(db, order.tenant_id)

    # ── Process based on payment status ──────────────────────────
    if payment_status == "SUCCESS":
        log.info("payment_success")
        order.mark_as_paid(
            payment_id=str(transaction_id),
            payment_method="kashier",
        )
        await order_repo.update(order)

    elif payment_status == "FAILED":
        error_msg = data.get("error", {}).get("message", "Payment failed")
        log.warning("payment_failed", error_message=error_msg)
        order.mark_payment_failed(reason=error_msg)
        await order_repo.update(order)

    else:
        log.info("webhook_status_no_action", status=payment_status)

    # Always return 200 to prevent gateway retries
    return {"status": "received", "transaction_id": transaction_id}
