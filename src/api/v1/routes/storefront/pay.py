"""Recovery payment page endpoints — pay for an existing (COD) order online.

Backs the storefront ``/pay/<order_id>`` page that the ``cod_recovery_offer_v1``
WhatsApp button deep-links to (see
``docs/whatsapp-templates/cod-recovery-offer-spec.md`` §5). A high-risk COD order
allowed under ``cod_trust.action == "recover"`` is nudged to convert COD →
prepaid; paying here is the conversion.

Public (no auth) — protected only by the order UUID + the store scope, exactly
like the order-tracking endpoint. Two verbs:

* ``GET  /storefront/store/{store_id}/pay/{order_id}`` — a sanitised view of the
  order plus the amount due, the merchant's recovery promo line, and the store's
  enabled online payment methods. Tells the page whether the order is still
  payable.
* ``POST /storefront/store/{store_id}/pay/{order_id}`` — initiate a gateway
  payment for the *existing* order (Paymob inline / Kashier redirect), reusing
  the same services checkout uses. Stamps ``metadata.cod_recovery_initiated`` so
  the gateway callback can attribute the recovery (``metadata.cod_recovered``)
  and the COD → prepaid conversion happens through the normal ``mark_as_paid``
  path in ``webhooks/paymob.py``.

Net-new is only the thin route + the recovery stamp; ``Order.mark_as_paid`` and
the gateway flow already exist. Monetary discount on the promo is intentionally
*not* applied here (v1 charges the full order total — the moat value is the
prepaid conversion, not the discount); the ``recovery_promo`` is shown as the
incentive copy. A real discount needs proper order-level adjustment modelling
and is a documented follow-up.
"""

from __future__ import annotations

import base64
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel

from src.api.dependencies.repositories import (
    get_order_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.config.settings import settings
from src.core.entities.order import OrderStatus, PaymentStatus
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.store_repository import StoreRepository

# Replay guard for payment initiation, mirroring the checkout route. The
# storefront already forwards `Idempotency-Key`, but this route never declared
# it, so FastAPI dropped it and two submits minted TWO gateway payment intents
# for the same order. Same TTL as checkout.
_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)
PAY_IDEMPOTENCY_TTL_SECONDS = 86_400  # 24 hours

logger = get_logger(__name__)

router = APIRouter()

# Online gateways the recovery page can initiate today. Manual methods
# (InstaPay proof, Fawry voucher) are a poor "pay now" experience and are
# excluded from v1 — COD is obviously never an option here.
_SUPPORTED_RECOVERY_GATEWAYS = ("paymob", "kashier")

# Paymob hosted "Unified Checkout" page — the redirect target built from the
# intention's public key + client secret (matches accept.paymob.com base).
_PAYMOB_UNIFIED_CHECKOUT = "https://accept.paymob.com/unifiedcheckout/"

# An order is still payable while it is open and unpaid.
_PAYABLE_STATUSES = frozenset({
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.PROCESSING,
})


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class PayLineItem(BaseModel):
    product_name: str
    quantity: int
    unit_price: int  # cents
    total: int  # cents


class PayOrderView(BaseModel):
    order_id: str
    order_number: str
    status: str
    payment_status: str
    currency: str
    total: int  # original order total (cents)
    amount_due: int  # what to pay now (== total in v1; discount deferred)
    # Money breakdown so "amount due" is explainable on the page
    # (items + shipping − discount; VAT is included in prices).
    subtotal: int = 0
    shipping_cost: int = 0
    discount_amount: int = 0
    is_payable: bool
    not_payable_reason: str | None = None  # already_paid / closed / null
    recovery_promo: str | None = None  # display copy only (no monetary effect v1)
    line_items: list[PayLineItem]
    enabled_payment_methods: list[str]  # online only, COD excluded
    # Manual transfer rails the store has configured (instapay /
    # vodafone_cash) — the page renders transfer instructions + proof
    # upload for these instead of a gateway redirect.
    manual_methods: list[str] = []
    store_name: str


class PayOrderRequest(BaseModel):
    """Which online method to pay the existing order with."""

    payment_method: (
        str  # paymob | paymob_card | paymob_wallet | kashier | instapay | vodafone_cash
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enabled_online_methods(store_settings: dict | None) -> list[str]:
    """Store's enabled gateways intersected with what recovery supports."""
    payment = (store_settings or {}).get("payment", {}) or {}
    out: list[str] = []
    for provider in _SUPPORTED_RECOVERY_GATEWAYS:
        cfg = payment.get(provider, {}) or {}
        if cfg.get("enabled"):
            out.append(provider)
    return out


_MANUAL_PAY_METHODS = ("instapay", "vodafone_cash")


def _enabled_manual_methods(store_settings: dict | None) -> list[str]:
    """Manual transfer rails with usable credentials (destination set)."""
    payment = (store_settings or {}).get("payment", {}) or {}
    out: list[str] = []
    for method in _MANUAL_PAY_METHODS:
        cfg = payment.get(method, {}) or {}
        if cfg.get("enabled"):
            out.append(method)
    return out


def _payable_state(order) -> tuple[bool, str | None]:
    """(is_payable, reason). Open + unpaid orders are payable."""
    pstatus = (
        order.payment_status.value
        if hasattr(order.payment_status, "value")
        else str(order.payment_status)
    )
    if pstatus == PaymentStatus.PAID.value:
        return False, "already_paid"
    if order.status not in _PAYABLE_STATUSES:
        return False, "closed"
    return True, None


async def _load_scoped_order(order_id: UUID, store_id: UUID, order_repo, store_repo):
    """Fetch the order and assert it belongs to this store. 404 otherwise.

    Mirrors the order-tracking 404 discipline: never reveal that an order
    exists under a different store.
    """
    order = await order_repo.get_by_id(order_id)
    if order is None or order.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )
    return order, store


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/pay/{order_id}",
    response_model=SuccessResponse[PayOrderView],
    summary="Recovery payment view for an existing order",
    operation_id="get_pay_order_view",
)
async def get_pay_order_view(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order UUID from the pay deep-link")],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Public, UUID-scoped view powering the ``/pay`` page. No PII or trust
    internals — just what the buyer needs to complete payment."""
    order, store = await _load_scoped_order(order_id, store_id, order_repo, store_repo)

    is_payable, reason = _payable_state(order)
    cod_trust = (store.settings or {}).get("cod_trust", {}) or {}

    items = [
        PayLineItem(
            product_name=li.product_name,
            quantity=li.quantity,
            unit_price=li.unit_price,
            total=li.quantity * li.unit_price,
        )
        for li in order.line_items
    ]

    return SuccessResponse(
        data=PayOrderView(
            order_id=str(order.id),
            order_number=order.order_number,
            status=order.status.value
            if hasattr(order.status, "value")
            else str(order.status),
            payment_status=order.payment_status.value
            if hasattr(order.payment_status, "value")
            else str(order.payment_status),
            currency=order.currency,
            total=order.total,
            amount_due=order.total,  # v1: full total; discount is a follow-up
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            discount_amount=order.discount_amount,
            is_payable=is_payable,
            not_payable_reason=reason,
            recovery_promo=cod_trust.get("recovery_promo"),
            line_items=items,
            enabled_payment_methods=_enabled_online_methods(store.settings),
            manual_methods=_enabled_manual_methods(store.settings),
            store_name=store.name,
        ),
        message="Pay view retrieved",
    )


@router.post(
    "/pay/{order_id}",
    response_model=SuccessResponse[dict],
    summary="Initiate online payment for an existing order",
    operation_id="initiate_pay_order",
)
async def initiate_pay_order(
    store_id: Annotated[UUID, Path(description="Store ID")],
    order_id: Annotated[UUID, Path(description="Order UUID")],
    request: PayOrderRequest,
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Create a gateway payment session for the existing order and stamp the
    recovery marker. The COD → prepaid conversion + ``cod_recovered``
    attribution happen in the gateway callback once payment succeeds."""
    # ── Idempotency check ──────────────────────────────────────────────
    # A retry (the shopper pressing pay again after a slow response) must
    # return the ORIGINAL payment session rather than opening a second one at
    # the gateway. Keyed per order so paying two different orders with the
    # same client key can't collide.
    pay_cache_key = (
        f"pay:idempotency:{store_id}:{order_id}:{idempotency_key}"
        if idempotency_key
        else None
    )
    if pay_cache_key and _cache_service:
        cached = await _cache_service.get(pay_cache_key)
        if cached:
            logger.info(f"Idempotent pay hit: order={order_id} key={idempotency_key}")
            return SuccessResponse(
                data=json.loads(cached),
                message="Payment already initiated",
            )

    order, store = await _load_scoped_order(order_id, store_id, order_repo, store_repo)

    # ── Manual transfer rails (InstaPay / Vodafone Cash) ───────────────
    # No gateway session: create-or-reuse the order's ManualPaymentIntent
    # (same machinery as checkout) and hand the page the transfer
    # instructions. The customer then uploads a proof via the existing
    # /orders/{order_id}/payment-proof endpoint using reference_code.
    if request.payment_method in _MANUAL_PAY_METHODS:
        is_payable, reason = _payable_state(order)
        if not is_payable:
            raise HTTPException(status_code=409, detail=reason or "not_payable")
        if request.payment_method not in _enabled_manual_methods(store.settings):
            raise HTTPException(
                status_code=422, detail="Method not enabled for this store"
            )
        data = await _initiate_manual(order, store, request.payment_method, order_repo)
        if pay_cache_key and _cache_service:
            await _cache_service.set(pay_cache_key, json.dumps(data), ttl=3600)
        return SuccessResponse(data=data, message="Transfer instructions ready")

    is_payable, reason = _payable_state(order)
    if not is_payable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This order can no longer be paid online ({reason}).",
        )

    method = (request.payment_method or "").lower()
    amount_due = order.total  # v1: full total
    currency = order.currency
    ship = order.shipping_address
    customer_email = (
        getattr(order, "customer_email", None)
        or getattr(order, "guest_email", None)
        or getattr(ship, "email", None)
    )

    if method.startswith("paymob"):
        result = await _initiate_paymob(
            order, store, amount_due, currency, ship, customer_email, order_repo
        )
    elif method == "kashier":
        result = await _initiate_kashier(
            order, store, amount_due, currency, customer_email, order_repo
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported payment method for online recovery.",
        )

    # Remember the session so a retry replays it instead of opening a second
    # one at the gateway. Best-effort: a cache failure must never fail a
    # payment we've already successfully created.
    if pay_cache_key and _cache_service:
        try:
            await _cache_service.set(
                pay_cache_key,
                json.dumps(result.data),
                expire=PAY_IDEMPOTENCY_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"pay_idempotency_cache_failed order={order_id} err={exc}")

    return result


async def _initiate_manual(order, store, method_value: str, order_repo) -> dict:
    """Create (or reuse) the order's manual-payment intent and return the
    transfer instructions payload for the pay page."""
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    from src.core.entities.instapay import ManualPaymentIntent, ManualPaymentMethod
    from src.infrastructure.external_services.manual_transfer import (
        ManualTransferPaymentService,
        generate_reference_code,
        get_merchant_manual_credentials,
    )
    from src.infrastructure.repositories.instapay_intent_repository import (
        ManualPaymentIntentRepository,
    )

    manual_method = ManualPaymentMethod(method_value)
    intent_repo = ManualPaymentIntentRepository(order_repo.session)

    # One intent per order (UNIQUE order_id): checkout may already have made
    # one, or a previous visit to this page did. Reuse it — its reference is
    # what a half-finished transfer would carry.
    existing = await intent_repo.get_by_order_id(order.id)
    if existing is not None:
        return {
            "type": "manual",
            "method": existing.method.value
            if hasattr(existing.method, "value")
            else str(existing.method),
            "destination": existing.display_destination,
            "display_phone": existing.display_phone,
            "reference_code": existing.reference_code,
            "amount_cents": existing.amount_cents,
            "qr_payload": existing.qr_payload,
        }

    credentials = await get_merchant_manual_credentials(store.settings, manual_method)
    manual_service = ManualTransferPaymentService(
        destination=credentials["destination"],
        method=manual_method,
        display_name=credentials.get("display_name"),
        fallback_phone=credentials.get("fallback_phone"),
        qr_image_url=credentials.get("qr_image_url"),
        qr_link_url=credentials.get("qr_link_url"),
    )

    intent_entity = None
    for _ in range(5):
        candidate = generate_reference_code(manual_method.reference_prefix)
        qr_payload, expires_at = manual_service.build_intent_payload(
            amount_cents=order.total,
            reference_code=candidate,
            note=f"Order {order.order_number}",
        )
        entity = ManualPaymentIntent.new(
            tenant_id=order.tenant_id,
            store_id=order.store_id,
            order_id=order.id,
            reference_code=candidate,
            method=manual_method,
            display_destination=credentials["destination"],
            display_phone=credentials.get("fallback_phone"),
            amount_cents=order.total,
            expires_at=expires_at,
            qr_payload=qr_payload,
        )
        try:
            async with order_repo.session.begin_nested():
                await intent_repo.create(entity)
            intent_entity = entity
            break
        except _IntegrityError:
            continue
    if intent_entity is None:
        # Lost every race — someone else created the intent; return theirs.
        existing = await intent_repo.get_by_order_id(order.id)
        if existing is None:
            raise HTTPException(
                status_code=500, detail="Could not prepare transfer instructions"
            )
        intent_entity = existing

    await _stamp_recovery_initiated(order, order_repo, str(intent_entity.id))
    await order_repo.session.commit()

    return {
        "type": "manual",
        "method": method_value,
        "destination": intent_entity.display_destination,
        "display_phone": intent_entity.display_phone,
        "reference_code": intent_entity.reference_code,
        "amount_cents": intent_entity.amount_cents,
        "qr_payload": intent_entity.qr_payload,
    }


async def _stamp_recovery_initiated(order, order_repo, payment_id: str) -> None:
    """Persist the payment ref + the recovery marker the callback keys off."""
    order.payment_id = payment_id
    order.metadata = {**(order.metadata or {}), "cod_recovery_initiated": True}
    await order_repo.update(order)


async def _initiate_paymob(
    order, store, amount_due, currency, ship, customer_email, order_repo
) -> SuccessResponse[dict]:
    """Mirror of the checkout Paymob block for an existing order."""
    try:
        from src.infrastructure.external_services.paymob.payment_service import (
            PaymobPaymentService,
            get_merchant_paymob_credentials,
        )

        credentials = await get_merchant_paymob_credentials(store.settings)
        paymob_service = PaymobPaymentService(
            secret_key=credentials["secret_key"],
            public_key=credentials["public_key"],
            hmac_secret=credentials["hmac_secret"],
            card_integration_id=credentials.get("card_integration_id"),
            wallet_integration_id=credentials.get("wallet_integration_id"),
            apple_pay_integration_id=credentials.get("apple_pay_integration_id"),
        )
        intent = await paymob_service.create_payment_intent(
            amount=amount_due,
            currency=currency,
            customer_email=str(customer_email) if customer_email else None,
            metadata={
                "order_id": str(order.id),
                "billing_data": {
                    "first_name": ship.first_name or "Customer",
                    "last_name": ship.last_name or "Customer",
                    "email": str(customer_email)
                    if customer_email
                    else "customer@example.com",
                    "phone_number": ship.phone or "+201000000000",
                    "city": ship.city or "NA",
                    "country": getattr(ship, "country", None) or "EG",
                    "street": ship.address_line1 or "NA",
                },
            },
        )
        await _stamp_recovery_initiated(order, order_repo, intent.id)
    except Exception as e:  # noqa: BLE001 — graceful gateway-down behaviour
        logger.error(f"Recovery Paymob initiation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online payment is not available right now. Please try again later.",
        )

    # Hosted Paymob Unified Checkout URL so the storefront can redirect
    # uniformly (no inline SDK). client_secret + public_key are also returned
    # for any surface that prefers the inline Pixel.
    payment_url = (
        f"{_PAYMOB_UNIFIED_CHECKOUT}?publicKey={credentials['public_key']}"
        f"&clientSecret={intent.client_secret}"
    )
    return SuccessResponse(
        data={
            "provider": "paymob",
            "type": "redirect",
            "payment_url": payment_url,
            "client_secret": intent.client_secret,
            "public_key": credentials["public_key"],
            "amount": f"{amount_due / 100:.2f}",
            "currency": currency,
            "order_id": str(order.id),
        },
        message="Payment initiated",
    )


async def _initiate_kashier(
    order, store, amount_due, currency, customer_email, order_repo
) -> SuccessResponse[dict]:
    """Mirror of the checkout Kashier block for an existing order."""
    try:
        from src.infrastructure.external_services.kashier.payment_service import (
            KashierPaymentService,
        )
        from src.infrastructure.external_services.secrets.secrets_manager import (
            get_secrets_manager,
        )

        kashier_settings = (store.settings or {}).get("payment", {}).get("kashier", {})
        if not kashier_settings.get("encrypted_credentials"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Kashier is not configured for this store.",
            )

        secrets_mgr = get_secrets_manager()
        key_id = kashier_settings["encryption_key_id"]
        encrypted = base64.b64decode(kashier_settings["encrypted_credentials"])
        creds = await secrets_mgr.decrypt(encrypted, key_id)

        kashier_service = KashierPaymentService(
            mid=creds["merchant_id"],
            api_key=creds["api_key"],
            secret_key=creds.get("secret_key"),
            apple_pay_enabled=kashier_settings.get("apple_pay_enabled", False),
        )
        intent = await kashier_service.create_payment_intent(
            amount=amount_due,
            currency=currency,
            customer_email=str(customer_email) if customer_email else None,
            metadata={"order_id": str(order.id)},
        )
        # Kashier keys off the order UUID as its merchantOrderId (mirrors
        # checkout.py), and the webhook resolves the order by it
        # (get_by_id(UUID(merchant_order_id))). The real gateway transaction id
        # is written by the callback on payment success — so we deliberately
        # stamp the order id here, NOT intent.id (which is the kashierOrderId).
        await _stamp_recovery_initiated(order, order_repo, str(order.id))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Recovery Kashier initiation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online payment is not available right now. Please try again later.",
        )

    return SuccessResponse(
        data={
            "provider": "kashier",
            "type": "session",
            "session_url": intent.client_secret,
            "amount": f"{amount_due / 100:.2f}",
            "currency": currency,
            "order_id": str(order.id),
        },
        message="Payment initiated",
    )
