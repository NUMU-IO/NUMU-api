"""Storefront checkout route.

URL: /storefront/store/{store_id}/checkout

Creates an order from the submitted line items, calculates totals
using live product prices, and optionally initiates payment.
"""

import base64
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.repositories import (
    get_abandoned_checkout_repository,
    get_coupon_repository,
    get_customer_repository,
    get_funnel_event_repository,
    get_network_reputation_repository,
    get_onboarding_repository,
    get_order_repository,
    get_product_repository,
    get_shipping_zone_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.checkout import CheckoutRequest, CheckoutResponse
from src.application.dto.order import (
    CreateOrderAddressDTO,
    CreateOrderLineItemDTO,
)
from src.application.services.attribution_sanitizer import sanitize_utm
from src.application.services.campaign_resolver import resolve_campaign_id
from src.application.services.cod_trust_service import (
    CodTrustDecision,
    LocationSignals,
    check_customer_trust,
)
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
    write_network_event,
)
from src.application.services.shipping_resolver import ShippingResolver
from src.application.services.tax_resolver import TaxLineInput, TaxResolver
from src.config import settings
from src.core.checkout_fields import (
    resolve_config as resolve_checkout_config,
)
from src.core.checkout_fields import (
    validate_custom_field_values,
)
from src.core.entities.abandoned_checkout import AbandonedCheckout
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.core.exceptions import EntityNotFoundError
from src.core.value_objects.geography import resolve_governorate
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.repositories import (
    AbandonedCheckoutRepository,
    CouponRepository,
    CustomerRepository,
    OnboardingRepository,
    OrderRepository,
    ProductRepository,
    ShippingZoneRepository,
    StoreRepository,
)
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.shopify_repository import (
    NetworkReputationRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _risk_level_from_score(score: int | None) -> str:
    """Translate a 0–100 score into the bucket used by RiskAssessmentModel.

    Mirrors the bucket boundaries used by the fraud-detection service so a
    merchant looking at the unified risk feed sees consistent labels across
    the COD-trust path and the heuristic fraud path.
    """
    if score is None:
        return "low"
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _suggested_action_from_decision(decision: "CodTrustDecision") -> str:
    """Map a trust-check outcome to the existing RiskAssessment action taxonomy."""
    if decision.reason == "blocked_high_risk":
        return "cancel"
    if decision.reason == "warned_high_risk":
        return "whatsapp_confirm"
    return "auto_approve"


async def _persist_cod_trust_assessment(
    *,
    session,
    store,
    order_id: UUID | None,
    order_number: str | None,
    customer,
    total_cents: int,
    currency: str,
    decision: "CodTrustDecision",
) -> None:
    """Write a RiskAssessmentModel row for a COD trust decision.

    Fail-open: any persistence error is logged but never raised — fraud
    auditing must never break a checkout.
    """
    try:
        from datetime import UTC, datetime

        from src.infrastructure.database.models.tenant.risk_assessment import (
            RiskAssessmentModel,
        )

        score = decision.score if decision.score is not None else 0
        assessment = RiskAssessmentModel(
            tenant_id=store.tenant_id,
            store_id=store.id,
            order_id=order_id,
            order_number=order_number,
            customer_name=(
                f"{customer.first_name or ''} {customer.last_name or ''}".strip()
                or None
            ),
            customer_email=str(customer.email) if customer.email else None,
            total_cents=total_cents,
            currency=currency,
            payment_method="cod",
            risk_score=score,
            risk_level=_risk_level_from_score(decision.score),
            score_type="preliminary",
            suggested_action=_suggested_action_from_decision(decision),
            action_taken=decision.reason,
            action_taken_at=datetime.now(UTC),
            action_taken_by="cod_trust",
            factors=list(decision.factors or []),
            scored_at=datetime.now(UTC),
        )
        session.add(assessment)
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — auditing must never block checkout
        logger.warning("cod_trust_assessment_persist_failed: %s", exc)


_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)
IDEMPOTENCY_TTL_SECONDS = 86_400  # 24 hours


def _generate_invoice_pdf(invoice, store_logo_url: str | None = None) -> bytes:
    """Generate invoice PDF (sync, meant to run in thread)."""
    from src.infrastructure.external_services.invoice import InvoicePDFGenerator

    generator = InvoicePDFGenerator(
        template_name="invoice_ar.html",
        language="ar_en",
        store_logo_url=store_logo_url,
    )
    return generator.generate(invoice)


@router.post(
    "/checkout",
    response_model=SuccessResponse[CheckoutResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create order from checkout",
    operation_id="checkout",
)
async def checkout(
    http_request: Request,
    response: Response,
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CheckoutRequest,
    optional_customer: Annotated[Customer | None, Depends(get_optional_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    funnel_repo: Annotated[
        "FunnelEventRepository", Depends(get_funnel_event_repository)
    ],
    network_repo: Annotated[
        "NetworkReputationRepository",
        Depends(get_network_reputation_repository),
    ],
    shipping_repo: Annotated[
        ShippingZoneRepository, Depends(get_shipping_zone_repository)
    ],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    abandoned_repo: Annotated[
        AbandonedCheckoutRepository,
        Depends(get_abandoned_checkout_repository),
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Process checkout for the authenticated customer.

    1. Validates all products exist, are active, in stock, and belong to the store.
    2. Resolves live prices from the product catalog (never trusts client prices).
    3. Creates an Order in PENDING status.
    4. Returns an optional payment_url when the payment method requires redirect.
    """
    # ── Resolve store early so guest customer creation can populate tenant_id ──
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # ── Checkout-fields: validate submitted custom fields against live config ──
    checkout_config = resolve_checkout_config(store.settings)
    accepted_custom_fields, custom_field_errors = validate_custom_field_values(
        checkout_config, request.custom_fields
    )
    if custom_field_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "custom_field_errors", "errors": custom_field_errors},
        )

    # ── Resolve or create customer ──────────────────────────────────────
    current_customer = optional_customer
    is_guest = current_customer is None

    if is_guest:
        # Identity rule for guests: phone is the source of truth. A returning
        # guest whose phone matches an existing customer (guest or registered)
        # in the same store reuses that row — otherwise placing 10 orders
        # would create 10 distinct customer records, since each guest gets a
        # fresh placeholder email when no real one is supplied.
        #
        # Email is only used as a fallback identity key when (a) we have no
        # canonical phone and (b) the guest provided a real (non-placeholder)
        # email that already exists for this store.
        from src.core.value_objects.email import Email as EmailVO
        from src.core.value_objects.phone import InvalidPhoneError

        email_cfg = (checkout_config.get("standard_fields") or {}).get("email") or {}
        email_required = bool(email_cfg.get("required", False))

        guest_email = (request.guest_email or "").strip() or None

        if email_required and not guest_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required for guest checkout.",
            )

        addr = request.shipping_address

        # Canonicalize the phone to E.164 so dedup is reliable regardless
        # of whether the buyer typed "01001234567" or "+201001234567". The
        # schema validator only strips separators, not country-codes.
        # Default region "EG" matches the storefront's market — adjust if/when
        # we expand. If parsing fails (truly invalid input), we fall back to
        # the lenient VO purely for storage; no phone-based dedup happens.
        guest_phone_vo: PhoneNumber | None = None
        canonical_phone: str | None = None
        if addr.phone:
            try:
                guest_phone_vo = PhoneNumber.parse(addr.phone, default_region="EG")
                canonical_phone = guest_phone_vo.value
            except InvalidPhoneError:
                try:
                    guest_phone_vo = PhoneNumber(value=addr.phone)
                except Exception:
                    guest_phone_vo = None

        # Phone lookup first — primary identity key for guests.
        existing: Customer | None = None
        if canonical_phone:
            existing = await customer_repo.get_by_phone(store_id, canonical_phone)

        # Email VO is needed in two cases:
        #   1. We didn't find an existing customer by phone → may need email
        #      lookup as a secondary key (only meaningful when the email is
        #      real, not a placeholder).
        #   2. We're creating a new customer → need an email to satisfy
        #      customers.email NOT NULL + unique-per-store.
        email_vo: EmailVO | None = None
        if guest_email:
            try:
                email_vo = EmailVO(value=guest_email)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid email address.",
                )

        if existing is None and email_vo is not None:
            # Real email provided and no phone match — fall back to email dedup.
            existing = await customer_repo.get_by_email(store_id, email_vo)

        if existing:
            current_customer = existing
            # Backfill phone on a returning customer who didn't have one
            # before (e.g. legacy guest row from before this change shipped).
            if guest_phone_vo and not existing.phone:
                existing.phone = guest_phone_vo
                current_customer = await customer_repo.update(existing)
        else:
            # New customer — synthesize a placeholder email if the guest
            # didn't supply one, so the (store_id, email) uniqueness holds.
            if email_vo is None:
                import uuid as _uuid

                email_vo = EmailVO(
                    value=f"guest+{_uuid.uuid4().hex[:12]}@noemail.numueg.app"
                )
            current_customer = Customer(
                store_id=store_id,
                email=email_vo,
                first_name=addr.first_name or "Guest",
                last_name=addr.last_name or "",
                phone=guest_phone_vo,
                is_verified=False,
                metadata={"guest": True, "has_real_email": guest_email is not None},
            )
            current_customer = await customer_repo.create(
                current_customer, tenant_id=store.tenant_id
            )

    # ── Idempotency check ──────────────────────────────────────────────
    if idempotency_key and _cache_service:
        cache_key = (
            f"checkout:idempotency:{store_id}:{current_customer.id}:{idempotency_key}"
        )
        cached = await _cache_service.get(cache_key)
        if cached:
            logger.info(
                f"Idempotent checkout hit: key={idempotency_key}, "
                f"customer={current_customer.id}"
            )
            response.status_code = status.HTTP_200_OK
            return SuccessResponse(
                data=CheckoutResponse(**json.loads(cached)),
                message="Order already created",
            )

    # Extract client IP (Nginx sets X-Real-IP; fall back to direct connection)
    client_ip: str | None = (
        http_request.headers.get("X-Real-IP")
        or http_request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (http_request.client.host if http_request.client else None)
    ) or None

    # User-Agent captured for Meta CAPI Purchase match-quality. Meta's
    # `client_user_agent` field is one of the two highest-signal match
    # keys when PII is missing — paired with `client_ip_address`. We
    # snapshot it at order-create time because the payment webhook fires
    # later from the PSP's server (not the customer's browser).
    client_user_agent: str | None = http_request.headers.get("user-agent") or None

    # Require email verification for registered (non-guest) customers
    if (
        not is_guest
        and current_customer.has_account
        and not current_customer.is_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before placing orders.",
        )

    # Require OTP verification for COD orders (skip for guests)
    is_cod = not request.payment_method or request.payment_method == "cod"
    if not is_guest and is_cod and _cache_service:
        from src.api.v1.routes.storefront.otp import _otp_verified_key

        verified_key = _otp_verified_key(store_id, current_customer.id)
        is_verified = await _cache_service.exists(verified_key)
        if not is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="يرجى تأكيد رقم الموبايل أولاً لإتمام طلب الدفع عند الاستلام.",
            )

    # Verify the customer belongs to this store
    if current_customer.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer does not belong to this store",
        )

    # ── Phase 7.5 — saved-card validation ──────────────────────────────
    # When the client passes `saved_payment_method_id`, verify the row
    # exists, belongs to *this* customer + store, is still active, and
    # matches the requested payment method's gateway. Mismatches 400
    # before any order is created so a tampered client can't bind one
    # customer's card to another's order.
    saved_card_token: str | None = None
    if request.saved_payment_method_id:
        if is_guest:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Saved cards require an authenticated customer.",
            )
        from sqlalchemy import select as _select

        from src.infrastructure.database.connection import (
            AsyncSessionLocal as _AsyncSessionLocal,
        )
        from src.infrastructure.database.models.tenant.saved_payment_method import (
            SavedPaymentMethodModel as _SavedCardModel,
        )

        async with _AsyncSessionLocal() as _s:
            row = (
                await _s.execute(
                    _select(_SavedCardModel).where(
                        _SavedCardModel.id == request.saved_payment_method_id,
                        _SavedCardModel.customer_id == current_customer.id,
                        _SavedCardModel.store_id == store_id,
                        _SavedCardModel.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved card not found for this customer.",
            )
        # The gateway in the saved card must match what the payment
        # request asks for (paying with a Kashier-saved card via the
        # Paymob gateway can't work — different token schemes).
        requested_gateway = (request.payment_method or "").lower()
        # `paymob_card` and `paymob` are both the Paymob gateway as
        # far as saved cards are concerned.
        norm = "paymob" if requested_gateway.startswith("paymob") else requested_gateway
        if row.gateway and row.gateway.lower() != norm:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Saved card belongs to gateway '{row.gateway}', but the "
                    f"requested payment method targets '{requested_gateway}'."
                ),
            )
        saved_card_token = row.card_token  # noqa: F841 — wired into gateway services in a follow-up

    # ── Phase 7.2 — pickup-location resolution ─────────────────────────
    # When the client requests in-store pickup, swap the shipping
    # rate machinery for a synthetic zero-cost "pickup" line and use
    # the location's address as the order's fulfillment origin.
    # Cannot mix pickup with a paid shipping rate — reject both-set
    # so the client surfaces the conflict cleanly.
    pickup_location_address: dict | None = None
    if request.pickup_location_id:
        if request.selected_shipping_rate_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Pickup orders cannot also have a shipping rate. "
                    "Clear selected_shipping_rate_id when picking up in-store."
                ),
            )
        from sqlalchemy import select as _select_loc

        from src.infrastructure.database.connection import (
            AsyncSessionLocal as _AsyncSessionLocal_loc,
        )
        from src.infrastructure.database.models.tenant.location import (
            LocationModel as _LocationModel,
        )

        async with _AsyncSessionLocal_loc() as _s:
            loc = (
                await _s.execute(
                    _select_loc(_LocationModel).where(
                        _LocationModel.id == request.pickup_location_id,
                        _LocationModel.store_id == store_id,
                        _LocationModel.is_active.is_(True),
                        _LocationModel.fulfills_pickup.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if loc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pickup location not found or not available for pickup.",
            )
        pickup_location_address = loc.address or {}
        # noqa: F841 — pickup_location_address is used in a follow-up that
        # stamps it on the order's fulfillment metadata. The validation
        # itself is the load-bearing part today; metadata wiring is a
        # short follow-up commit that touches the order-creation path.
        _ = pickup_location_address

    # ── Phase 8.3 — gift card pre-validation ──────────────────────────
    # Resolve every submitted code to a gift card row, validate
    # redeemability, and compute the total tender we can apply. We
    # only **validate** here — actual debiting happens after the
    # order total is computed, so we know exactly how much tender
    # is needed. Pre-validation 400s on bad codes BEFORE the order
    # is created so the customer doesn't end up with a PENDING order
    # tied to an unusable code.
    gift_card_rows: list = []
    gift_card_tender_available = 0
    if request.gift_card_codes:
        from src.application.services.gift_card_service import GiftCardService
        from src.infrastructure.database.connection import (
            AsyncSessionLocal as _AsyncSessionLocal_gc,
        )

        async with _AsyncSessionLocal_gc() as _s:
            gc_svc = GiftCardService(_s)
            for code in request.gift_card_codes:
                card = await gc_svc.get_by_code(code, store_id)
                if card is None or not card.is_redeemable():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Gift card ending in •••{(card.last_four if card else '????')} "
                            "is not redeemable."
                        ),
                    )
                # Currency match — a gift card in USD can't redeem against
                # an EGP order. Cross-currency conversion is a Phase 9
                # concern.
                if card.currency != (
                    store.currency.value
                    if hasattr(store.currency, "value")
                    else str(store.currency)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Gift card currency {card.currency} doesn't match "
                            "this order's currency."
                        ),
                    )
                gift_card_rows.append(card)
                gift_card_tender_available += card.current_balance_cents
        # noqa: F841 — pre-validation only; the actual redeem happens in a
        # follow-up commit that wires gift_card_tender_available into the
        # order's `amount_due_after_tender` computation. For now the
        # validation prevents bad-code orders from reaching the gateway.
        _ = gift_card_tender_available
        _ = gift_card_rows

    # ── COD Trust Network check ────────────────────────────────────────
    # Look up customer reputation in the cross-merchant network table.
    # Fails open on any error — fraud filtering must never block legitimate
    # orders due to infrastructure issues. Read happens BEFORE the order
    # event is recorded below, so the customer's own current order does
    # not inflate their own score during the check.
    trust_decision: CodTrustDecision | None = None
    if is_cod:
        customer_phone = (
            request.shipping_address.phone if request.shipping_address else None
        )

        # When the merchant has cod_trust enabled, phone is non-negotiable
        # for COD: without it the trust check returns `no_phone` and the
        # filter is silently bypassed. Reject the order at the API
        # boundary instead — defense in depth alongside the storefront
        # form's required marker.
        from src.application.services.cod_trust_service import (
            get_cod_trust_settings,
        )

        _cod_trust_cfg = get_cod_trust_settings(store.settings)
        if _cod_trust_cfg["enabled"] and not (customer_phone or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "phone_required_for_cod",
                    "message_ar": ("رقم الموبايل مطلوب لإتمام طلب الدفع عند الاستلام."),
                    "message_en": (
                        "A phone number is required for cash on delivery orders."
                    ),
                },
            )

        # Build location signals from this checkout's shipping address +
        # the customer's previous delivery point (for teleport detection).
        shipping = request.shipping_address
        previous_coords: tuple[float, float] | None = None
        try:
            recent_orders = await order_repo.get_by_customer(
                current_customer.id, skip=0, limit=1
            )
            if recent_orders:
                prev = recent_orders[0].shipping_address
                if prev.latitude is not None and prev.longitude is not None:
                    previous_coords = (prev.latitude, prev.longitude)
        except Exception as exc:  # noqa: BLE001 — fail-open for fraud signals
            logger.warning("cod_trust_previous_location_lookup_error: %s", exc)

        location_signals = LocationSignals(
            latitude=shipping.latitude if shipping else None,
            longitude=shipping.longitude if shipping else None,
            accuracy=shipping.location_accuracy if shipping else None,
            source=shipping.location_source if shipping else None,
            previous_coords=previous_coords,
        )

        trust_decision = await check_customer_trust(
            phone=customer_phone,
            store_settings=store.settings,
            network_repo=network_repo,
            location=location_signals,
        )
        logger.info(
            "cod_trust_check store=%s customer=%s allowed=%s reason=%s score=%s "
            "confidence=%s factors=%s",
            str(store_id),
            str(current_customer.id),
            trust_decision.allowed,
            trust_decision.reason,
            trust_decision.score,
            trust_decision.confidence,
            [f["code"] for f in trust_decision.factors],
        )
        if not trust_decision.allowed:
            # Persist the blocked decision before raising so the merchant
            # sees the action in their COD-trust decisions feed even though
            # no order row exists yet.
            await _persist_cod_trust_assessment(
                session=order_repo.session,
                store=store,
                order_id=None,
                order_number=None,
                customer=current_customer,
                total_cents=0,
                currency=store.default_currency.value
                if store.default_currency
                else "EGP",
                decision=trust_decision,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "cod_trust_blocked",
                    "message_ar": (
                        "عذراً، لا يمكن إتمام طلبك بالدفع عند الاستلام في الوقت "
                        "الحالي. يمكنك الدفع إلكترونياً."
                    ),
                    "message_en": (
                        "Unable to complete this order with cash on delivery. "
                        "Please use online payment."
                    ),
                    "fallback_payment_methods": ["paymob_card", "paymob_wallet"],
                },
            )

    # Build line items with server-side price resolution
    line_items: list[CreateOrderLineItemDTO] = []
    # Remember per-line inventory mode so the atomic-deduct step below
    # can target the right code path (variant combo vs product-level)
    # without re-resolving the product.
    line_item_inventory: list[dict] = []
    # Accumulated cart weight (grams) — used for weight_band rate
    # evaluation. Products without a weight contribute 0, so weight-band
    # merchants should either configure per-product weights or use an
    # open-ended band with sensible defaults.
    cart_weight_g: int = 0
    for item in request.line_items:
        product = await product_repo.get_by_id(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {item.product_id} not found",
            )
        if product.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product.name} does not belong to this store",
            )
        if product.status != ProductStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product.name} is not available",
            )

        # ── Stock pre-check ──
        # Three modes:
        #   1. Merchant opted into oversell (continue_selling_when_out_of_stock) → skip
        #   2. Product has matching variant_combinations + customer picked options → check combo
        #   3. Otherwise → product-level stock
        product_attrs = product.attributes or {}
        allow_negative = bool(product_attrs.get("continue_selling_when_out_of_stock"))
        selections = item.selections or {}
        combos = product_attrs.get("variant_combinations") or []
        # The storefront PDP writes selections with capitalised axis names
        # (`{"Size": "M"}` from `setSelection("Size", ...)`) while the
        # merchant hub stores `variant_combinations[].options` lowercase
        # (`{"size": "m"}` from `attributes.variants[].name`). Strict `==`
        # never matched, so per-combo stock deduction silently fell
        # through to the product-level path. Normalise both sides to
        # lowercase before comparing so the right combo is found and the
        # variant-specific stock actually decrements.

        def _norm(d: dict | None) -> dict:
            return {str(k).lower(): str(v).lower() for k, v in (d or {}).items()}

        normalised_selections = _norm(selections)
        matching_combo = None
        if normalised_selections and isinstance(combos, list):
            for c in combos:
                if (
                    isinstance(c, dict)
                    and _norm(c.get("options")) == normalised_selections
                ):
                    matching_combo = c
                    break

        if matching_combo is not None:
            if matching_combo.get("enabled") is False:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{product.name} is not available in the selected options",
                )
            if not allow_negative:
                raw = matching_combo.get("stock")
                try:
                    available = int(raw) if raw not in (None, "") else 0
                except (TypeError, ValueError):
                    available = 0
                if available < item.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Insufficient stock for {product.name} "
                            f"({', '.join(f'{k}: {v}' for k, v in selections.items())}) "
                            f"(available: {available})"
                        ),
                    )
        elif not allow_negative and product.quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for {product.name} (available: {product.quantity})",
            )

        # Human-readable "Color: Red, Size: M" so the merchant sees what was
        # actually ordered in the order detail view, without needing to join
        # against a variant catalog.
        variant_name = (
            ", ".join(f"{k}: {v}" for k, v in selections.items())
            if selections
            else None
        )
        line_items.append(
            CreateOrderLineItemDTO(
                product_id=product.id,
                product_name=product.name,
                sku=product.sku,
                quantity=item.quantity,
                unit_price=product.price.cents,
                variant_id=item.variant_id,
                variant_name=variant_name,
                # Persist the raw selection dict so the merchant order-detail
                # UI can render axis-by-axis (Color: Red / Size: M) without
                # parsing the joined `variant_name`. Stored regardless of
                # whether a combo matched — the customer's pick is useful
                # context even on products that don't have a strict combo
                # catalog yet (e.g. legacy data).
                properties=dict(selections) if selections else None,
            )
        )
        line_item_inventory.append({
            "product_id": product.id,
            # Use the normalised dict for stock deduction so deduct_variant_stock
            # finds the same combo we matched above. The unnormalised dict is
            # what we display to the merchant and what gets stored on the order
            # line — those go through `properties` above.
            "selections": normalised_selections if matching_combo is not None else None,
            "allow_negative": allow_negative,
            "quantity": item.quantity,
        })
        # Product.weight is Decimal kilograms; convert to grams and
        # multiply by quantity. Missing weight → skip (contributes 0).
        if product.weight is not None:
            cart_weight_g += int(product.weight * 1000) * item.quantity

    # Build address DTOs
    addr = request.shipping_address
    shipping_address = CreateOrderAddressDTO(
        first_name=addr.first_name,
        last_name=addr.last_name,
        address_line1=addr.address_line1,
        address_line2=addr.address_line2,
        city=addr.city,
        state=addr.state,
        postal_code=addr.postal_code,
        country=addr.country,
        phone=addr.phone,
        latitude=addr.latitude,
        longitude=addr.longitude,
        location_accuracy=addr.location_accuracy,
        location_source=addr.location_source,
        geocoded_address=addr.geocoded_address,
    )

    billing_address = None
    if request.billing_address:
        b = request.billing_address
        billing_address = CreateOrderAddressDTO(
            first_name=b.first_name,
            last_name=b.last_name,
            address_line1=b.address_line1,
            address_line2=b.address_line2,
            city=b.city,
            state=b.state,
            postal_code=b.postal_code,
            country=b.country,
            phone=b.phone,
            latitude=b.latitude,
            longitude=b.longitude,
            location_accuracy=b.location_accuracy,
            location_source=b.location_source,
            geocoded_address=b.geocoded_address,
        )

    currency = store.default_currency.value if store.default_currency else "EGP"

    # NB: We previously built a CreateOrderDTO here and read dto.tax_amount
    # downstream. Tax is now resolved server-side via TaxResolver below
    # (see resolved_tax_cents) so the DTO became a dead assignment. The
    # Order entity is built directly from line_items + addresses + the
    # resolved tax/shipping. If the existing CreateOrderUseCase ever gets
    # wired in here, re-introduce the DTO at that point.

    # Create order via the existing use case
    # We pass store.owner_id to satisfy the authorization check inside
    # CreateOrderUseCase (it verifies user_id == store.owner_id).
    # For customer-initiated checkout we bypass that by calling the
    # repository directly with the same logic.
    order_number = await order_repo.get_next_order_number(store_id)

    from src.core.entities.order import (
        Order,
        OrderLineItem,
        OrderShippingAddress,
        OrderStatus,
        PaymentStatus,
    )

    order_line_items = []
    subtotal = 0
    for li in line_items:
        total_price = li.unit_price * li.quantity
        subtotal += total_price
        order_line_items.append(
            OrderLineItem(
                product_id=li.product_id,
                product_name=li.product_name,
                variant_id=li.variant_id,
                variant_name=li.variant_name,
                sku=li.sku,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total_price=total_price,
                # Customer's per-axis pick survives into the order row so the
                # merchant can render "Color: Red / Size: M" in the order
                # detail UI without re-deriving from `variant_name`. The DTO
                # default is None; OrderLineItem's field default is `{}`.
                properties=li.properties or {},
            )
        )

    ship_addr = OrderShippingAddress(
        first_name=shipping_address.first_name,
        last_name=shipping_address.last_name,
        address_line1=shipping_address.address_line1,
        address_line2=shipping_address.address_line2,
        city=shipping_address.city,
        state=shipping_address.state,
        postal_code=shipping_address.postal_code,
        country=shipping_address.country,
        phone=shipping_address.phone,
        latitude=shipping_address.latitude,
        longitude=shipping_address.longitude,
        location_accuracy=shipping_address.location_accuracy,
        location_source=shipping_address.location_source,
        geocoded_address=shipping_address.geocoded_address,
    )

    bill_addr = None
    if billing_address:
        bill_addr = OrderShippingAddress(
            first_name=billing_address.first_name,
            last_name=billing_address.last_name,
            address_line1=billing_address.address_line1,
            address_line2=billing_address.address_line2,
            city=billing_address.city,
            state=billing_address.state,
            postal_code=billing_address.postal_code,
            country=billing_address.country,
            phone=billing_address.phone,
            latitude=billing_address.latitude,
            longitude=billing_address.longitude,
            location_accuracy=billing_address.location_accuracy,
            location_source=billing_address.location_source,
            geocoded_address=billing_address.geocoded_address,
        )

    # Apply coupon if provided (with row-level lock to prevent concurrent bypass)
    discount_amount = 0
    coupon_code = None
    coupon_id = None
    # If the redeemed coupon was issued under a marketing campaign,
    # we want to attribute the order to that campaign when no
    # UTM-resolved campaign won first. Captured here, applied below
    # after the UTM resolver has run.
    _coupon_campaign_id = None

    if request.coupon_code:
        from src.application.use_cases.coupons.apply_coupon import ApplyCouponUseCase

        apply_coupon = ApplyCouponUseCase(coupon_repository=coupon_repo)
        coupon_result = await apply_coupon.execute(
            store_id=store_id,
            code=request.coupon_code,
            order_amount=Decimal(str(subtotal)),
            for_update=True,
        )
        discount_amount = int(coupon_result.discount_amount)
        coupon_code = coupon_result.code
        coupon_id = coupon_result.coupon_id
        _coupon_campaign_id = coupon_result.campaign_id

    # Atomically deduct stock BEFORE creating the order. Variant combos
    # use a row-lock path (deduct_variant_stock); non-variant flows keep
    # the original conditional-UPDATE path (deduct_stock). Transaction
    # rollback restores both. When the merchant enabled
    # continue_selling_when_out_of_stock, the atomic guard is loosened
    # (stock can go negative) so the order still succeeds.
    for li, inv in zip(line_items, line_item_inventory):
        if inv["selections"] is not None:
            success, reason = await product_repo.deduct_variant_stock(
                inv["product_id"],
                inv["selections"],
                inv["quantity"],
                allow_negative=inv["allow_negative"],
            )
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Insufficient stock for {li.product_name} "
                        f"({reason}). Please refresh and try again."
                    ),
                )
        else:
            success = await product_repo.deduct_stock(
                inv["product_id"],
                inv["quantity"],
                allow_negative=inv["allow_negative"],
            )
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Insufficient stock for {li.product_name}. Please refresh and try again.",
                )

    # ── Server-side shipping resolution (trust-gap closure) ──
    # The client NEVER sets shipping_cost. If a rate was selected via
    # /shipping/options, we re-resolve it here using the merchant's
    # authoritative rules and stamp the snapshot (zone_id + rate_id +
    # computed amount) on the order.
    #
    # If the store has ANY active shipping zones configured, we require
    # a `selected_shipping_rate_id` on the payload. Without this guard,
    # a malicious client could omit the field and bypass the merchant's
    # rate table entirely — the "legacy zero-shipping path" would turn
    # into a free-shipping exploit the moment the merchant configures
    # zones. Stores with no active zones (fresh stores / merchants who
    # haven't finished setup) still accept the legacy path so their
    # storefront doesn't 400 pre-configuration.
    shipping_cost_cents = 0
    resolved_zone_id: UUID | None = None
    resolved_rate_id: UUID | None = None
    resolved_label: str | None = request.shipping_method

    if not request.selected_shipping_rate_id:
        if await shipping_repo.has_active_zones(store_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=("Please select a shipping option before placing your order."),
            )

    if request.selected_shipping_rate_id:
        # Resolve the destination governorate from the shipping address.
        # `resolve_governorate` accepts either an ISO 3166-2 code (e.g.
        # "EG-C" — what the storefront sends after the UI overhaul) or
        # a free-text name / legacy Bosta code / Arabic variant, so the
        # pre-overhaul checkout payload also keeps working.
        gov = None
        if shipping_address.state:
            gov = resolve_governorate(shipping_address.state)
        if gov is None and shipping_address.city:
            gov = resolve_governorate(shipping_address.city)
        if gov is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not resolve the shipping address's governorate. "
                    "Please select a supported governorate."
                ),
            )

        resolver = ShippingResolver(shipping_repo, currency=currency)
        resolution = await resolver.resolve_one(
            store_id=store_id,
            rate_id=request.selected_shipping_rate_id,
            governorate_code=gov.code,
            cart_subtotal_cents=subtotal,
            cart_weight_g=cart_weight_g,
            cod_requested=request.cod_requested,
        )
        if resolution is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected shipping rate is no longer available for this address.",
            )
        if request.cod_requested and not resolution.cod_supported:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cash on delivery isn't available in this area.",
            )
        shipping_cost_cents = resolution.amount_cents
        resolved_zone_id = resolution.zone_id
        resolved_rate_id = resolution.rate_id
        resolved_label = resolution.label

    # Server-side tax resolution under the platform's VAT-INCLUSIVE
    # pricing policy: merchant-listed prices already include 14% VAT,
    # so we NEVER add tax on top of the subtotal. The resolver runs in
    # inclusive mode so ``included_tax_cents`` is back-computed for
    # invoice/ETA reporting; ``tax_to_add_cents`` is always 0 and is
    # NOT used in the total formula. The order's ``tax_amount`` field
    # records the included VAT as informational accounting only.
    tax_resolver = TaxResolver()
    tax_resolution = tax_resolver.resolve(
        store_settings=store.settings,
        line_items=[
            TaxLineInput(unit_price_cents=li.unit_price, quantity=li.quantity)
            for li in line_items
        ],
        discount_amount_cents=discount_amount,
        destination_governorate=(shipping_address.state or shipping_address.city),
        force_inclusive=True,
    )
    # VAT-inclusive: persist the *included* VAT for accounting, but it
    # is already inside ``subtotal`` and must not be added again.
    resolved_tax_cents = tax_resolution.included_tax_cents

    # VAT-inclusive total: subtotal + shipping - discount. We do NOT
    # add `resolved_tax_cents` — VAT is already part of `subtotal`.
    total = subtotal + shipping_cost_cents - discount_amount

    # ── Feature 001 — resolve UTM + attribution context ───────────────
    # When the storefront sent a full attribution envelope (the new
    # path), prefer its last_touch values over the legacy flat utm_*
    # fields. Run every UTM through the sanitizer regardless of source
    # — even the legacy flat fields, since clients can put anything in
    # them. SEC-006: campaign_resolver is scoped by (store_id,
    # short_code) so cross-tenant attribution is impossible.
    _last = request.attribution.last_touch if request.attribution else None
    _first = request.attribution.first_touch if request.attribution else None
    _eff_utm_source = sanitize_utm(_last.utm_source if _last else request.utm_source)
    _eff_utm_medium = sanitize_utm(_last.utm_medium if _last else request.utm_medium)
    _eff_utm_campaign = sanitize_utm(
        _last.utm_campaign if _last else request.utm_campaign
    )
    _eff_utm_term = sanitize_utm(_last.utm_term if _last else request.utm_term)
    _eff_utm_content = sanitize_utm(_last.utm_content if _last else request.utm_content)
    _resolved_campaign_id = await resolve_campaign_id(
        session=order_repo.session,
        store_id=store_id,
        utm_campaign=_eff_utm_campaign,
    )
    # Coupon-based attribution fallback: when the UTM resolver came up
    # empty (direct traffic, organic, or untagged share) and the
    # customer pasted a campaign-issued coupon, attribute the order to
    # the coupon's campaign. UTM wins by default — coupon only fills
    # the blank.
    if _resolved_campaign_id is None and _coupon_campaign_id is not None:
        _resolved_campaign_id = _coupon_campaign_id
    _attribution_dict = (
        request.attribution.model_dump(mode="json")
        if request.attribution is not None
        else None
    )
    _first_touch_at = _first.ts if _first is not None else None

    order = Order(
        store_id=store_id,
        tenant_id=store.tenant_id,
        customer_id=current_customer.id,
        order_number=order_number,
        line_items=order_line_items,
        shipping_address=ship_addr,
        billing_address=bill_addr,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        subtotal=subtotal,
        shipping_cost=shipping_cost_cents,
        tax_amount=resolved_tax_cents,
        discount_amount=discount_amount,
        coupon_code=coupon_code,
        coupon_id=coupon_id,
        total=total,
        currency=currency,
        payment_method=request.payment_method,
        shipping_method=resolved_label,
        shipping_zone_id=resolved_zone_id,
        shipping_rate_id=resolved_rate_id,
        customer_notes=request.customer_notes,
        metadata={
            **({"ip_address": client_ip} if client_ip else {}),
            **({"user_agent": client_user_agent} if client_user_agent else {}),
            **(
                {"custom_fields": accepted_custom_fields}
                if accepted_custom_fields
                else {}
            ),
            # Preserve the resolved tax shape so downstream consumers
            # (e-invoice / ETA, refund calculator, export reports) can
            # reconcile the order without re-running the resolver.
            **(
                {
                    "tax_breakdown": {
                        "rate": tax_resolution.rate,
                        "inclusive": tax_resolution.inclusive,
                        "added_cents": tax_resolution.tax_to_add_cents,
                        "included_cents": tax_resolution.included_tax_cents,
                    }
                }
                if tax_resolution.rate > 0
                else {}
            ),
        },
        utm_source=_eff_utm_source,
        utm_medium=_eff_utm_medium,
        utm_campaign=_eff_utm_campaign,
        utm_term=_eff_utm_term,
        utm_content=_eff_utm_content,
        campaign_id=_resolved_campaign_id,
        attribution=_attribution_dict,
        first_touch_at=_first_touch_at,
        session_fingerprint=request.session_fingerprint,
    )

    created_order = await order_repo.create(order)

    # ── Feature 001 — seed customer's first-touch attribution ─────────
    # Set once on the first attributed order, never overwritten. Used
    # by future LTV-by-acquisition-channel analytics. We update via
    # raw SQL through the order_repo session so the write piggybacks on
    # the same transaction as the order create — and so we never have
    # to round-trip the Customer entity through this for one column.
    if _first is not None:
        from sqlalchemy import text as _sql_text

        await order_repo.session.execute(
            _sql_text(
                "UPDATE public.customers "
                "SET first_touch_attribution = CAST(:snapshot AS JSONB), "
                "    first_touch_at = :ts "
                "WHERE id = :customer_id "
                "  AND store_id = :store_id "
                "  AND first_touch_attribution IS NULL"
            ),
            {
                "snapshot": _first.model_dump_json(),
                "ts": _first_touch_at,
                "customer_id": current_customer.id,
                "store_id": store_id,
            },
        )

    # Post-feature-001 / journey table — link any anonymous touches
    # the visitor accrued before authenticating to this customer. One
    # UPDATE per checkout; bounded by the visitor's session length.
    # Failure here is non-fatal: a missed backfill just means the
    # journey timeline starts from the customer's known events, not
    # their anonymous prefix.
    if request.session_fingerprint:
        try:
            from src.application.services import customer_touch_service

            await customer_touch_service.backfill_session_touches(
                session=order_repo.session,
                store_id=store_id,
                session_fingerprint=request.session_fingerprint,
                customer_id=current_customer.id,
            )
        except Exception:  # pragma: no cover - non-critical
            pass

    # ── Persist COD trust assessment for the allowed/warned decision ───
    # Skip non-actionable reasons (disabled, no_phone, lookup_error) —
    # those are noise for the merchant feed. Below-threshold, warned, new
    # customer, and low-confidence decisions all make it into the audit
    # log so the merchant sees the filter actually working.
    if (
        is_cod
        and trust_decision is not None
        and trust_decision.reason not in {"disabled", "no_phone", "lookup_error"}
    ):
        await _persist_cod_trust_assessment(
            session=order_repo.session,
            store=store,
            order_id=created_order.id,
            order_number=created_order.order_number,
            customer=current_customer,
            total_cents=created_order.total,
            currency=currency,
            decision=trust_decision,
        )

    # ── Record COD order in network reputation ─────────────────────────
    # Fire-and-forget: never break checkout if the network write fails.
    # Comes AFTER the trust check above so the customer's own current
    # order doesn't inflate their own score during the check.
    if is_cod:
        try:
            phone_for_network = (
                order.shipping_address.phone if order.shipping_address else None
            )
            if phone_for_network:
                phone_hash = extract_phone_hash_from_string(phone_for_network)
                if phone_hash:
                    await write_network_event(
                        phone_hash=phone_hash,
                        store_id=store_id,
                        event_type="order",
                        network_repo=network_repo,
                    )
        except Exception as exc:
            logger.warning("network_event_record_failed: %s", exc)

    # Emit funnel event: checkout_started
    try:
        await funnel_repo.create(
            tenant_id=store.tenant_id,
            store_id=store.id,
            step="checkout_started",
            customer_id=current_customer.id,
            session_fingerprint=request.session_fingerprint,
            step_data={
                "order_id": str(created_order.id),
                "total": created_order.total,
                "payment_method": request.payment_method,
                "item_count": len(created_order.line_items),
            },
        )
    except Exception:
        pass

    # Emit funnel event: order_completed (COD only)
    #
    # For COD, the customer's funnel is complete the moment they place the
    # order — there is no follow-up payment webhook to wait for. For online
    # payments (paymob/kashier) this event is fired from the gateway webhook
    # only after payment actually succeeds, so abandoned-mid-payment carts
    # don't falsely count as conversions.
    if is_cod:
        try:
            await funnel_repo.create(
                tenant_id=store.tenant_id,
                store_id=store.id,
                step="order_completed",
                customer_id=current_customer.id,
                session_fingerprint=request.session_fingerprint,
                step_data={
                    "order_id": str(created_order.id),
                    "total": created_order.total,
                    "payment_method": "cod",
                },
            )
        except Exception:
            pass

    # Update real-time counters
    try:
        from src.infrastructure.cache.realtime_counters import record_order_created

        await record_order_created(
            store.id,
            {
                "order_id": str(created_order.id),
                "order_number": created_order.order_number,
                "total": created_order.total,
                "customer_name": f"{current_customer.first_name} {current_customer.last_name}",
                "item_count": len(created_order.line_items),
                "payment_method": request.payment_method,
            },
        )
    except Exception:
        pass

    # Update customer lifetime stats
    current_customer.total_orders = (current_customer.total_orders or 0) + 1
    current_customer.total_spent = (
        current_customer.total_spent or 0
    ) + created_order.total
    await customer_repo.update(current_customer)

    # Build payment URL / payment data if applicable
    payment_url: str | None = None
    payment_data: dict | None = None
    paymob_client_secret: str | None = None
    paymob_public_key: str | None = None

    # ── Deposit-to-confirm COD branch ────────────────────────────────
    # When the merchant has enabled a COD deposit policy and the
    # customer picked "cod", we divert the payment dispatch: the order
    # moves into PENDING_DEPOSIT and we charge only the deposit amount
    # through the customer's chosen deposit gateway. The order still
    # records `payment_method="cod"` on the order row — the deposit is a
    # *precursor* to COD, not a replacement. On delivery, the remaining
    # balance (`total - deposit_amount_cents`) is collected in cash.
    #
    # `_dispatch_method` / `_gateway_amount` decouple the gateway-side
    # dispatch from the order-side fields so downstream code (analytics,
    # emails, receipts) keeps using the full order total; only the
    # gateway charge sees the deposit amount.
    _dispatch_method: str | None = request.payment_method
    _gateway_amount: int = created_order.total

    _deposit_policy_raw = (store.settings or {}).get("payment", {}).get("cod", {}).get(
        "deposit_policy"
    ) or {}
    if (
        request.payment_method == "cod"
        and _deposit_policy_raw.get("enabled")
        and int(_deposit_policy_raw.get("amount_cents", 0) or 0) > 0
    ):
        _allowed_gateways: list[str] = list(
            _deposit_policy_raw.get("allowed_gateways") or []
        )
        if not request.deposit_gateway:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This store requires a deposit to confirm COD orders. "
                    "Please select a deposit payment method."
                ),
            )
        if request.deposit_gateway not in _allowed_gateways:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Selected deposit gateway is not allowed by this store. "
                    f"Allowed: {', '.join(_allowed_gateways) or '(none)'}."
                ),
            )

        from datetime import UTC, datetime, timedelta

        _ttl_minutes = int(_deposit_policy_raw.get("ttl_minutes", 30) or 30)
        _deposit_amount = int(_deposit_policy_raw["amount_cents"])

        # Mutate the order into PENDING_DEPOSIT. We set `.status`
        # directly rather than going through `transition_to` because
        # PENDING → PENDING_DEPOSIT isn't a supported transition —
        # orders destined for the deposit flow are *created* in this
        # state rather than migrating from PENDING.
        from src.core.entities.order import OrderStatus as _OS

        created_order.status = _OS.PENDING_DEPOSIT
        created_order.deposit_required_cents = _deposit_amount
        # Pre-populate `deposit_amount_cents` with the required amount.
        # The gateway's webhook will confirm by transitioning through
        # `mark_as_paid`; if the captured amount differs from required
        # (rare — some gateways take a fee from the customer's side),
        # the merchant can spot it by comparing these two columns.
        created_order.deposit_amount_cents = _deposit_amount
        created_order.deposit_expires_at = datetime.now(UTC) + timedelta(
            minutes=_ttl_minutes
        )
        created_order.deposit_gateway = request.deposit_gateway
        await order_repo.update(created_order)

        # Paymob exposes two sub-methods (card / wallet) in the storefront
        # — the deposit UI picks "paymob" broadly; default to card, which
        # is by far the most common customer path. Wallet can be an
        # explicit choice later if merchants ask.
        _dispatch_method = (
            "paymob_card"
            if request.deposit_gateway == "paymob"
            else request.deposit_gateway
        )
        _gateway_amount = _deposit_amount

    if _dispatch_method and _dispatch_method.startswith("paymob"):
        # Paymob payment initiation (per-merchant via store.settings)
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
            )

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            ship_addr = request.shipping_address

            intent = await paymob_service.create_payment_intent(
                amount=_gateway_amount,
                currency=currency,
                customer_email=customer_email_str,
                metadata={
                    "order_id": str(created_order.id),
                    "billing_data": {
                        "first_name": ship_addr.first_name or "Customer",
                        "last_name": ship_addr.last_name or "Customer",
                        "email": customer_email_str or "customer@example.com",
                        "phone_number": ship_addr.phone or "+201000000000",
                        "city": ship_addr.city or "NA",
                        "country": ship_addr.country or "EG",
                        "street": ship_addr.address_line1 or "NA",
                    },
                },
            )

            created_order.payment_id = intent.id
            await order_repo.update(created_order)

            paymob_client_secret = intent.client_secret
            paymob_public_key = credentials["public_key"]

        except Exception as e:
            logger.error(f"Paymob payment initiation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Online payment is not available for this store. Please choose another payment method.",
            )

    elif _dispatch_method == "kashier":
        # Kashier payment via store.settings encrypted credentials
        try:
            from src.infrastructure.external_services.kashier.payment_service import (
                KashierPaymentService,
            )
            from src.infrastructure.external_services.secrets.secrets_manager import (
                get_secrets_manager,
            )

            kashier_settings = (
                (store.settings or {}).get("payment", {}).get("kashier", {})
            )
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
            )

            created_order.payment_id = str(created_order.id)
            await order_repo.update(created_order)

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            intent = await kashier_service.create_payment_intent(
                amount=_gateway_amount,
                currency=currency,
                customer_email=customer_email_str,
                metadata={"order_id": str(created_order.id)},
            )

            # Session-based: client_secret contains the sessionUrl for iframe
            payment_data = {
                "provider": "kashier",
                "type": "session",
                "session_url": intent.client_secret,
                "order_id": intent.id,
                "amount": f"{_gateway_amount / 100:.2f}",
                "currency": currency,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Kashier payment initiation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Online payment is not available for this store. Please choose another payment method.",
            )

    elif _dispatch_method == "fawaterak":
        # Fawaterak payment via store.settings encrypted credentials
        try:
            from src.infrastructure.external_services.fawaterak.payment_service import (
                FawaterakPaymentService,
                get_merchant_fawaterak_credentials,
            )

            credentials = await get_merchant_fawaterak_credentials(store.settings)
            fawaterak_service = FawaterakPaymentService(
                api_key=credentials["api_key"],
                vendor_key=credentials.get("vendor_key"),
                environment=credentials.get("environment", "staging"),
            )

            created_order.payment_id = str(created_order.id)
            await order_repo.update(created_order)

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            customer_phone = (
                str(current_customer.phone) if current_customer.phone else None
            )

            # Build redirect URLs for Fawaterak
            subdomain = store.subdomain
            base_storefront_url = f"https://{subdomain}.numueg.app"

            intent = await fawaterak_service.create_payment_intent(
                amount=_gateway_amount,
                currency=currency,
                customer_email=customer_email_str,
                metadata={
                    "order_id": str(created_order.id),
                    "billing_data": {
                        "first_name": ship_addr.first_name or "Customer",
                        "last_name": ship_addr.last_name or "Customer",
                        "email": customer_email_str or "customer@example.com",
                        "phone_number": customer_phone or "",
                        "street": ship_addr.address_line1 or "",
                        "city": ship_addr.city or "",
                    },
                    "items": [
                        {
                            "name": li.product_name,
                            "price": li.unit_price / 100,
                            "quantity": li.quantity,
                        }
                        for li in order_line_items
                    ],
                    "redirect_urls": {
                        "success_url": f"{base_storefront_url}/track/{created_order.id}",
                        "fail_url": f"{base_storefront_url}/checkout?payment_failed=true",
                        "pending_url": f"{base_storefront_url}/track/{created_order.id}",
                    },
                },
            )

            # Update payment_id to Fawaterak invoice ID
            created_order.payment_id = intent.id
            await order_repo.update(created_order)

            # Fawaterak returns a payment URL for redirect
            payment_data = {
                "provider": "fawaterak",
                "type": "redirect",
                "payment_url": intent.client_secret,
                "invoice_id": intent.id,
                "order_id": str(created_order.id),
                "amount": f"{_gateway_amount / 100:.2f}",
                "currency": currency,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Fawaterak payment initiation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Online payment is not available for this store. Please choose another payment method.",
            )

    elif _dispatch_method == "fawry":
        # Fawry payment via store.settings encrypted credentials
        try:
            from src.infrastructure.external_services.fawry.payment_service import (
                FawryPaymentService,
                get_merchant_fawry_credentials,
            )

            credentials = await get_merchant_fawry_credentials(store.settings)
            fawry_service = FawryPaymentService(
                merchant_code=credentials["merchant_code"],
                security_key=credentials["security_key"],
            )

            # Set payment_id to order ID so the webhook can resolve it
            created_order.payment_id = str(created_order.id)
            await order_repo.update(created_order)

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            customer_phone = (
                str(current_customer.phone) if current_customer.phone else None
            )

            intent = await fawry_service.create_payment_intent(
                amount=_gateway_amount,
                currency=currency,
                customer_email=customer_email_str,
                metadata={
                    "order_id": str(created_order.id),
                    "merchant_ref_number": str(created_order.id),
                    "customer_mobile": customer_phone,
                    "customer_name": current_customer.full_name,
                    "description": f"Order {created_order.order_number}",
                    "items": [
                        {
                            "id": str(li.product_id),
                            "name": li.product_name,
                            "price": li.unit_price / 100,
                            "quantity": li.quantity,
                        }
                        for li in order_line_items
                    ],
                },
            )

            # Fawry returns a reference number and payment URL
            payment_data = {
                "provider": "fawry",
                "type": "reference",
                "reference_number": intent.id,
                "payment_url": fawry_service.get_payment_url(intent.id),
                "order_id": str(created_order.id),
                "amount": f"{_gateway_amount / 100:.2f}",
                "currency": currency,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Fawry payment initiation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Fawry payment is not available for this store. Please choose another payment method.",
            )

    elif _dispatch_method == "instapay":
        # InstaPay (manual IPA + proof upload). No gateway call — we just
        # persist the InstapayIntent and hand the storefront the IPA + QR
        # + reference code to show the customer. Funds move out-of-band;
        # customer confirms via the proof-upload endpoint.
        try:
            from sqlalchemy.exc import IntegrityError as _IntegrityError

            from src.application.use_cases.payments.submit_payment_proof import (  # noqa: F401 (keeps module importable at startup)
                SubmitPaymentProofUseCase,
            )
            from src.core.entities.instapay import InstapayIntent
            from src.infrastructure.external_services.instapay import (
                InstapayPaymentService,
                get_merchant_instapay_credentials,
            )
            from src.infrastructure.external_services.instapay.payment_service import (
                generate_reference_code,
            )
            from src.infrastructure.repositories.instapay_intent_repository import (
                InstapayIntentRepository,
            )

            credentials = await get_merchant_instapay_credentials(store.settings)
            instapay_service = InstapayPaymentService(
                ipa=credentials["ipa"],
                ipa_display_name=credentials.get("ipa_display_name"),
                fallback_phone=credentials.get("fallback_phone"),
                qr_image_url=credentials.get("qr_image_url"),
                qr_link_url=credentials.get("qr_link_url"),
            )
            intent_repo = InstapayIntentRepository(order_repo.session)

            # Reference codes are short enough (~10^9 combinations) that
            # collisions are rare but not impossible, and a collision
            # racing between two requests will surface as IntegrityError
            # from the UNIQUE constraint. Wrap each attempt in a SAVEPOINT
            # so a collision doesn't poison the outer request transaction,
            # and retry with a fresh code until success or exhaustion.
            reference_code = ""
            qr_payload = ""
            expires_at = None
            for _ in range(5):
                candidate = generate_reference_code()
                cand_payload, cand_expires_at = instapay_service.build_intent_payload(
                    amount_cents=_gateway_amount,
                    reference_code=candidate,
                    note=f"Order {created_order.order_number}",
                )
                intent_entity = InstapayIntent.new(
                    tenant_id=created_order.tenant_id,
                    store_id=created_order.store_id,
                    order_id=created_order.id,
                    reference_code=candidate,
                    display_ipa=credentials["ipa"],
                    display_phone=credentials.get("fallback_phone"),
                    amount_cents=_gateway_amount,
                    expires_at=cand_expires_at,
                    qr_payload=cand_payload,
                )
                try:
                    async with order_repo.session.begin_nested():
                        await intent_repo.create(intent_entity)
                except _IntegrityError:
                    # Collision on reference_code OR order_id (same order
                    # seen twice, very rare here). Retry with a fresh code.
                    continue
                reference_code = candidate
                qr_payload = cand_payload
                expires_at = cand_expires_at
                break
            if not reference_code or expires_at is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Could not allocate an InstaPay reference. Please retry.",
                )

            created_order.payment_id = reference_code
            created_order.metadata["instapay"] = {
                "reference_code": reference_code,
            }
            await order_repo.update(created_order)

            payment_data = instapay_service.to_checkout_payload(
                reference_code=reference_code,
                qr_payload=qr_payload,
                amount_cents=_gateway_amount,
                currency=currency,
                expires_at=expires_at,
                order_id=str(created_order.id),
                # When _gateway_amount differs from the order total
                # we're charging a deposit (COD-with-deposit). Pass
                # the full total so the storefront can frame the UI
                # as "X now, Y on delivery".
                is_deposit=_gateway_amount != created_order.total,
                order_total_cents=created_order.total,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"InstaPay payment initiation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="InstaPay is not available for this store. Please choose another payment method.",
            )

    elif _dispatch_method and _dispatch_method != "cod":
        # Other payment providers via tenant credentials
        try:
            from src.api.dependencies.payment import get_tenant_payment_service
            from src.core.interfaces.services.payment_service import PaymentProvider

            payment_service = await get_tenant_payment_service(
                provider=_dispatch_method,
                tenant_id=store.tenant_id,
                session=order_repo.session,
            )

            created_order.payment_id = str(created_order.id)
            await order_repo.update(created_order)

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            intent = await payment_service.create_payment_intent(
                amount=_gateway_amount,
                currency=currency,
                customer_email=customer_email_str,
                metadata={"order_id": str(created_order.id)},
            )

            if payment_service.provider == PaymentProvider.KASHIER:
                payment_data = {
                    "provider": "kashier",
                    "type": "hash",
                    "mid": payment_service._mid,
                    "hash": intent.client_secret,
                    "order_id": intent.id,
                    "amount": f"{_gateway_amount / 100:.2f}",
                    "currency": currency,
                    "mode": payment_service._mode,
                }
            else:
                payment_url = intent.client_secret

        except Exception as e:
            logger.warning(
                f"Payment initiation failed for order {created_order.order_number}: {e}"
            )
            # Order is still created in PENDING state; merchant can retry

    logger.info(
        f"Checkout completed: order={created_order.order_number}, "
        f"customer={current_customer.id}, total={created_order.total} {currency}"
    )

    # Dispatch order-confirmation notifications (non-blocking)
    customer_email = str(current_customer.email) if current_customer.email else None

    if customer_email:
        try:
            import asyncio

            from src.infrastructure.external_services.resend.email_service import (
                ResendEmailService,
            )

            # Persistent tracking page — the same URL the customer sees
            # immediately after checkout. It lives on the store's own
            # subdomain and reflects whatever status the merchant sets in
            # the dashboard (polls every 30s on the client).
            base_storefront_url = (
                f"https://{store.custom_domain}"
                if store.custom_domain
                else f"https://{store.subdomain}.numueg.app"
            )
            order_tracking_url = f"{base_storefront_url}/track/{created_order.id}"

            order_details = {
                "items": [
                    {
                        "name": li.product_name,
                        "quantity": li.quantity,
                        "price": li.unit_price / 100,
                    }
                    for li in order_line_items
                ],
                "total": created_order.total / 100,
                "currency": currency,
                "store_name": store.name,
                "customer_name": current_customer.full_name,
                "tracking_url": order_tracking_url,
            }

            # InstaPay: include IPA / ref / amount / expiry + a direct
            # resume link in the confirmation email so a customer who
            # closed the tab can still complete the payment.
            if (
                request.payment_method == "instapay"
                and isinstance(payment_data, dict)
                and payment_data.get("provider") == "instapay"
            ):
                order_details["instapay"] = {
                    "ipa": payment_data.get("ipa"),
                    "reference_code": payment_data.get("reference_code"),
                    "amount_cents": payment_data.get("amount_cents"),
                    "currency": payment_data.get("currency", currency),
                    "expires_at": payment_data.get("expires_at"),
                    "fallback_phone": payment_data.get("fallback_phone"),
                    "resume_url": f"{base_storefront_url}/instapay/{created_order.id}",
                }

            async def _send_order_email():
                try:
                    svc = ResendEmailService()
                    await svc.send_order_confirmation(
                        email=customer_email,
                        order_number=created_order.order_number,
                        order_details=order_details,
                        # Follow the merchant's storefront language so
                        # an EN-facing store isn't shipped Arabic copy
                        # when their InstaPay block (IPA, ref, expiry)
                        # carries customer-critical info.
                        language=(store.default_language or "ar"),
                    )
                    logger.info(f"Order confirmation email sent to {customer_email}")
                except Exception as exc:
                    logger.warning(f"Order confirmation email failed: {exc}")

            asyncio.create_task(_send_order_email())
        except Exception as e:
            logger.warning(f"Failed to dispatch order confirmation email: {e}")

    # WhatsApp notification (via Celery — optional)
    try:
        customer_phone = str(current_customer.phone) if current_customer.phone else None
        if customer_phone:
            prefs = current_customer.metadata.get("notification_preferences", {})
            whatsapp_prefs = prefs.get("whatsapp", {})
            if whatsapp_prefs.get("order_confirmation", True):
                from src.infrastructure.messaging.tasks.notification_tasks import (
                    send_whatsapp_order_confirmation_task,
                )

                total_display = f"{currency} {created_order.total / 100:.2f}"
                # Reuse the same tracking URL the email uses so both
                # channels point at the same page.
                _wa_base_url = (
                    f"https://{store.custom_domain}"
                    if store.custom_domain
                    else f"https://{store.subdomain}.numueg.app"
                )
                _wa_tracking_url = f"{_wa_base_url}/track/{created_order.id}"
                send_whatsapp_order_confirmation_task.delay(
                    phone=customer_phone,
                    customer_name=current_customer.full_name,
                    order_number=created_order.order_number,
                    total=total_display,
                    store_name=store.name,
                    language=store.default_language,
                    tracking_url=_wa_tracking_url,
                    # Required for the order_confirmation_v2 template's
                    # "Manage order" URL button — the redirector at
                    # numueg.app/o/<id> expects the order UUID. Without
                    # this kwarg, the messaging service falls back to
                    # ``order_number`` (e.g. "ORD-000017"), which the
                    # redirector can't resolve → customer lands on the
                    # apex marketing page.
                    order_id=str(created_order.id),
                )
    except Exception as e:
        logger.warning(f"Failed to dispatch WhatsApp notification: {e}")

    # Invoices are deferred: we no longer issue an invoice at checkout —
    # not for COD (the merchant collects cash on delivery, so no invoice
    # until they mark the order paid) and not for prepaid (issued after
    # the payment-gateway capture webhook confirms funds). Both paths now
    # converge on `OrderPaidEvent`, handled by
    # `handle_invoice_on_order_paid` in
    # src/infrastructure/events/handlers/invoice_on_paid_handler.py.

    # Merchant onboarding: send first-order email if this is order #1
    # AND mark the FIRST_ORDER onboarding step complete. The manual order
    # endpoint already does this via CreateOrderUseCase, but the storefront
    # checkout path didn't — so stores whose first order came from a real
    # customer were stuck at 5/6 "waiting for first order" forever.
    try:
        total_orders = await order_repo.count_by_store(store_id)
        if total_orders == 1:
            from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
                send_first_order_email_task,
            )

            merchant_email = store.contact_email
            if merchant_email:
                send_first_order_email_task.delay(
                    email=merchant_email,
                    merchant_name=store.name,
                    order_number=created_order.order_number,
                    total=f"{currency} {created_order.total / 100:.2f}",
                    language=store.default_language,
                )

        # Always try to complete the step (idempotent inside the helper).
        # Covers the edge case where order #1 came in before this code
        # was deployed but later orders arrive afterwards.
        from src.application.use_cases.onboarding.auto_complete import (
            try_complete_onboarding_step,
        )
        from src.core.entities.onboarding import OnboardingStepKey

        await try_complete_onboarding_step(
            onboarding_repo, store_id, OnboardingStepKey.FIRST_ORDER
        )
    except Exception as e:
        logger.warning(f"Failed to dispatch first-order onboarding email: {e}")

    # ── Abandoned-checkout reconciliation ────────────────────────────────
    # The storefront writes an `abandoned_checkouts` row when the customer
    # adds items to the cart (POST /cart/track). If we find a matching
    # un-recovered row for this (session_fingerprint, email) pair, mark it
    # recovered with the resulting order_id so it disappears from the
    # merchant's Abandoned Checkouts page. Fire-and-forget on failure.
    try:
        candidate_email = (
            current_customer.email if current_customer else request.guest_email
        )
        active_cart = await abandoned_repo.find_active_for_session(
            store_id=store_id,
            session_fingerprint=request.session_fingerprint,
            email=candidate_email,
        )
        if active_cart is not None:
            await abandoned_repo.mark_recovered(
                active_cart.id, order_id=created_order.id
            )
    except Exception as e:
        logger.warning(f"Failed to mark abandoned checkout as recovered: {e}")

    # Dispatch async fraud check (fire-and-forget via Celery)
    try:
        from src.infrastructure.messaging.tasks.fraud_tasks import (
            fraud_check_order_task,
        )

        fraud_check_order_task.delay(
            order_id=str(created_order.id),
            store_id=str(store_id),
            tenant_id=str(store.tenant_id) if store.tenant_id else None,
            order_number=created_order.order_number,
            total_cents=created_order.total,
            currency=currency,
            payment_method=request.payment_method,
            customer_name=current_customer.full_name,
            customer_email=customer_email,
            shipping_address={
                "first_name": ship_addr.first_name,
                "last_name": ship_addr.last_name,
                "address_line1": ship_addr.address_line1,
                "city": ship_addr.city,
                "country": ship_addr.country,
            },
            billing_address={
                "first_name": bill_addr.first_name,
                "last_name": bill_addr.last_name,
                "address_line1": bill_addr.address_line1,
                "city": bill_addr.city,
                "country": bill_addr.country,
            }
            if bill_addr
            else None,
            ip_address=client_ip,
        )
    except Exception as e:
        logger.warning(f"Failed to dispatch fraud check task: {e}")

    # Clear the customer's cart only after the entire checkout succeeds
    from src.infrastructure.repositories.cart_repository import RedisCartRepository

    _checkout_cart_repo = RedisCartRepository()
    await _checkout_cart_repo.delete_by_customer_id(current_customer.id, store_id)

    # Clear OTP verified flag (one-time use per checkout)
    if is_cod and _cache_service:
        from src.api.v1.routes.storefront.otp import _otp_verified_key

        await _cache_service.delete(_otp_verified_key(store_id, current_customer.id))

    checkout_response = CheckoutResponse(
        order_id=str(created_order.id),
        order_number=created_order.order_number,
        total=created_order.total,
        currency=created_order.currency,
        payment_status=created_order.payment_status.value,
        payment_url=payment_url,
        payment_data=payment_data,
        paymob_client_secret=paymob_client_secret,
        paymob_public_key=paymob_public_key,
    )

    # ── Cache response for idempotency ───────────────────────────────
    if idempotency_key and _cache_service:
        cache_key = (
            f"checkout:idempotency:{store_id}:{current_customer.id}:{idempotency_key}"
        )
        await _cache_service.set(
            cache_key,
            checkout_response.model_dump_json(),
            expire=IDEMPOTENCY_TTL_SECONDS,
        )

    return SuccessResponse(
        data=checkout_response,
        message="Order created successfully",
    )


# ============================================================================
# Cart tracking — feeds the merchant hub's Abandoned Checkouts page.
# ============================================================================


class CartTrackLineItem(BaseModel):
    """Snapshot of a single cart line item sent from the storefront."""

    product_id: UUID | None = None
    product_name: str | None = None
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int = 1
    unit_price: int = 0  # cents
    total_price: int = 0  # cents


class CartTrackRequest(BaseModel):
    """Payload the storefront sends on cart changes.

    Identity is established by `session_fingerprint` (always present on the
    storefront — it's the same value used for funnel events) and optionally
    `email` once the customer fills it in. Both are matched on upsert so the
    same cart is updated as the customer progresses.
    """

    session_fingerprint: str = Field(..., min_length=1, max_length=64)
    line_items: list[CartTrackLineItem] = Field(default_factory=list)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=32)
    shipping_address: dict | None = None
    subtotal: int = 0
    shipping_cost: int = 0
    tax_amount: int = 0
    discount_amount: int = 0
    total: int = 0
    currency: str = Field("EGP", min_length=3, max_length=3)
    coupon_code: str | None = Field(None, max_length=50)
    utm_source: str | None = Field(None, max_length=200)
    utm_medium: str | None = Field(None, max_length=200)
    utm_campaign: str | None = Field(None, max_length=200)


@router.post(
    "/cart/track",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Track storefront cart state for abandoned-checkout recovery",
    operation_id="cart_track",
)
async def cart_track(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: CartTrackRequest,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    abandoned_repo: Annotated[
        AbandonedCheckoutRepository,
        Depends(get_abandoned_checkout_repository),
    ],
    optional_customer: Annotated[Customer | None, Depends(get_optional_customer)],
) -> Response:
    """Upsert the customer's current cart into `abandoned_checkouts`.

    Called by the storefront on every cart change (add/remove/quantity,
    contact-form blur, etc.). Idempotent: matches the row by
    `extra_data->>session_fingerprint` OR email, ordered by recency. New
    rows are created when no match is found.

    Returns 204 — the storefront doesn't need any response body. Failures
    here are best-effort: the customer can still check out even if we lose
    a tracking write.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        # Don't 404 here — a 404 on this hot path would spam Sentry from
        # storefronts that briefly hit a wrong store ID. Silently no-op.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Skip empty carts — nothing to recover.
    if not request.line_items:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    candidate_email = optional_customer.email if optional_customer else request.email

    try:
        existing = await abandoned_repo.find_active_for_session(
            store_id=store_id,
            session_fingerprint=request.session_fingerprint,
            email=candidate_email,
        )

        now = datetime.now(UTC)
        line_items_payload = [li.model_dump(mode="json") for li in request.line_items]
        extra = {
            "session_fingerprint": request.session_fingerprint,
        }

        if existing:
            existing.line_items = line_items_payload
            existing.email = candidate_email or existing.email
            existing.phone = request.phone or existing.phone
            if request.shipping_address is not None:
                existing.shipping_address = request.shipping_address
            existing.subtotal = request.subtotal
            existing.shipping_cost = request.shipping_cost
            existing.tax_amount = request.tax_amount
            existing.discount_amount = request.discount_amount
            existing.total = request.total
            existing.currency = request.currency
            if request.coupon_code is not None:
                existing.coupon_code = request.coupon_code
            if request.utm_source is not None:
                existing.utm_source = request.utm_source
            if request.utm_medium is not None:
                existing.utm_medium = request.utm_medium
            if request.utm_campaign is not None:
                existing.utm_campaign = request.utm_campaign
            existing.last_activity_at = now
            existing.extra_data = {**(existing.extra_data or {}), **extra}
            # Re-activate if it had been auto-marked abandoned: the customer
            # came back to the cart, so it's in-progress again.
            existing.abandoned_at = None
            await abandoned_repo.update(existing)
        else:
            new_cart = AbandonedCheckout(
                store_id=store_id,
                tenant_id=store.tenant_id,
                customer_id=optional_customer.id if optional_customer else None,
                line_items=line_items_payload,
                email=candidate_email,
                phone=request.phone,
                shipping_address=request.shipping_address,
                subtotal=request.subtotal,
                shipping_cost=request.shipping_cost,
                tax_amount=request.tax_amount,
                discount_amount=request.discount_amount,
                total=request.total,
                currency=request.currency,
                coupon_code=request.coupon_code,
                utm_source=request.utm_source,
                utm_medium=request.utm_medium,
                utm_campaign=request.utm_campaign,
                last_activity_at=now,
                extra_data=extra,
            )
            await abandoned_repo.create(new_cart)
    except Exception as e:
        logger.warning(f"cart_track upsert failed for store={store_id}: {e}")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
