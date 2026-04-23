"""Storefront checkout route.

URL: /storefront/store/{store_id}/checkout

Creates an order from the submitted line items, calculates totals
using live product prices, and optionally initiates payment.
"""

import base64
import json
import logging
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

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_customer_repository,
    get_funnel_event_repository,
    get_network_reputation_repository,
    get_order_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.checkout import CheckoutRequest, CheckoutResponse
from src.application.dto.order import (
    CreateOrderAddressDTO,
    CreateOrderDTO,
    CreateOrderLineItemDTO,
)
from src.application.services.cod_trust_service import (
    LocationSignals,
    check_customer_trust,
)
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
    write_network_event,
)
from src.config import settings
from src.core.checkout_fields import (
    resolve_config as resolve_checkout_config,
)
from src.core.checkout_fields import (
    validate_custom_field_values,
)
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.repositories import (
    CouponRepository,
    CustomerRepository,
    OrderRepository,
    ProductRepository,
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
        # Email requirement follows the merchant's checkout-fields config —
        # required only if the merchant marked email as required. If email is
        # disabled entirely, we synthesize a per-order placeholder so the
        # Customer row (which requires a unique email per store) can be
        # created; notifications fall back to phone/WhatsApp.
        from src.core.value_objects.email import Email as EmailVO

        email_cfg = (checkout_config.get("standard_fields") or {}).get("email") or {}
        email_enabled = bool(email_cfg.get("enabled", True))
        email_required = bool(email_cfg.get("required", False))

        guest_email = (request.guest_email or "").strip() or None

        if email_required and not guest_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required for guest checkout.",
            )

        if guest_email:
            try:
                email_vo = EmailVO(value=guest_email)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid email address.",
                )
        elif not email_enabled or not email_required:
            # Merchant opted out of collecting email. Generate a stable
            # placeholder so the customers.email uniqueness constraint holds.
            import uuid as _uuid

            email_vo = EmailVO(
                value=f"guest+{_uuid.uuid4().hex[:12]}@noemail.numueg.app"
            )
        else:
            # Email was enabled-but-not-required and none was supplied; treat
            # as opt-out path to keep a consistent placeholder.
            import uuid as _uuid

            email_vo = EmailVO(
                value=f"guest+{_uuid.uuid4().hex[:12]}@noemail.numueg.app"
            )

        # Re-use existing customer record if same email+store, else create.
        # Placeholder emails never collide (fresh UUID per order).
        existing = await customer_repo.get_by_email(store_id, email_vo)
        if existing:
            current_customer = existing
        else:
            addr = request.shipping_address
            current_customer = Customer(
                store_id=store_id,
                email=email_vo,
                first_name=addr.first_name or "Guest",
                last_name=addr.last_name or "",
                phone=None,
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

    # ── COD Trust Network check ────────────────────────────────────────
    # Look up customer reputation in the cross-merchant network table.
    # Fails open on any error — fraud filtering must never block legitimate
    # orders due to infrastructure issues. Read happens BEFORE the order
    # event is recorded below, so the customer's own current order does
    # not inflate their own score during the check.
    if is_cod:
        customer_phone = (
            request.shipping_address.phone if request.shipping_address else None
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
        matching_combo = None
        if selections and isinstance(combos, list):
            for c in combos:
                if isinstance(c, dict) and c.get("options") == selections:
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
            )
        )
        line_item_inventory.append({
            "product_id": product.id,
            "selections": selections if matching_combo is not None else None,
            "allow_negative": allow_negative,
            "quantity": item.quantity,
        })

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

    dto = CreateOrderDTO(
        customer_id=current_customer.id,
        line_items=line_items,
        shipping_address=shipping_address,
        billing_address=billing_address,
        currency=currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
    )

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
                sku=li.sku,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total_price=total_price,
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

    total = subtotal + dto.shipping_cost + dto.tax_amount - discount_amount

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
        shipping_cost=dto.shipping_cost,
        tax_amount=dto.tax_amount,
        discount_amount=discount_amount,
        coupon_code=coupon_code,
        coupon_id=coupon_id,
        total=total,
        currency=currency,
        payment_method=request.payment_method,
        shipping_method=request.shipping_method,
        customer_notes=request.customer_notes,
        metadata={
            **({"ip_address": client_ip} if client_ip else {}),
            **(
                {"custom_fields": accepted_custom_fields}
                if accepted_custom_fields
                else {}
            ),
        },
        utm_source=request.utm_source,
        utm_medium=request.utm_medium,
        utm_campaign=request.utm_campaign,
        session_fingerprint=request.session_fingerprint,
    )

    created_order = await order_repo.create(order)

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

    if request.payment_method and request.payment_method.startswith("paymob"):
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
                amount=total,
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

    elif request.payment_method == "kashier":
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
                amount=created_order.total,
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
                "amount": f"{created_order.total / 100:.2f}",
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

    elif request.payment_method == "fawaterak":
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
                amount=created_order.total,
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
                "amount": f"{created_order.total / 100:.2f}",
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

    elif request.payment_method == "fawry":
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
                amount=created_order.total,
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
                "amount": f"{created_order.total / 100:.2f}",
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

    elif request.payment_method and request.payment_method != "cod":
        # Other payment providers via tenant credentials
        try:
            from src.api.dependencies.payment import get_tenant_payment_service
            from src.core.interfaces.services.payment_service import PaymentProvider

            payment_service = await get_tenant_payment_service(
                provider=request.payment_method,
                tenant_id=store.tenant_id,
                session=order_repo.session,
            )

            created_order.payment_id = str(created_order.id)
            await order_repo.update(created_order)

            customer_email_str = (
                str(current_customer.email) if current_customer.email else None
            )
            intent = await payment_service.create_payment_intent(
                amount=created_order.total,
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
                    "amount": f"{created_order.total / 100:.2f}",
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

            async def _send_order_email():
                try:
                    svc = ResendEmailService()
                    await svc.send_order_confirmation(
                        email=customer_email,
                        order_number=created_order.order_number,
                        order_details=order_details,
                        language="ar",  # NUMU emails are Egyptian Arabic only for now
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
    except Exception as e:
        logger.warning(f"Failed to dispatch first-order onboarding email: {e}")

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
