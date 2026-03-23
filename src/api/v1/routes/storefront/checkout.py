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

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_customer_repository,
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
from src.config import settings
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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Process checkout for the authenticated customer.

    1. Validates all products exist, are active, in stock, and belong to the store.
    2. Resolves live prices from the product catalog (never trusts client prices).
    3. Creates an Order in PENDING status.
    4. Returns an optional payment_url when the payment method requires redirect.
    """
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

    # Require email verification for account-based customers
    if current_customer.has_account and not current_customer.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before placing orders.",
        )

    # Verify the customer belongs to this store
    if current_customer.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer does not belong to this store",
        )

    # Validate store
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Build line items with server-side price resolution
    line_items: list[CreateOrderLineItemDTO] = []
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
        if product.quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock for {product.name} (available: {product.quantity})",
            )

        line_items.append(
            CreateOrderLineItemDTO(
                product_id=product.id,
                product_name=product.name,
                sku=product.sku,
                quantity=item.quantity,
                unit_price=product.price.cents,
                variant_id=item.variant_id,
            )
        )

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

    # Atomically deduct stock BEFORE creating the order.
    # Uses conditional UPDATE (WHERE quantity >= needed) so concurrent
    # checkouts cannot oversell. If the transaction rolls back, stock
    # is automatically restored.
    for li in line_items:
        success = await product_repo.deduct_stock(li.product_id, li.quantity)
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
        metadata={"ip_address": client_ip} if client_ip else {},
    )

    created_order = await order_repo.create(order)

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
            }

            async def _send_order_email():
                try:
                    svc = ResendEmailService()
                    await svc.send_order_confirmation(
                        email=customer_email,
                        order_number=created_order.order_number,
                        order_details=order_details,
                        language=store.default_language,
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
                send_whatsapp_order_confirmation_task.delay(
                    phone=customer_phone,
                    customer_name=current_customer.full_name,
                    order_number=created_order.order_number,
                    total=total_display,
                    store_name=store.name,
                    language=store.default_language,
                )
    except Exception as e:
        logger.warning(f"Failed to dispatch WhatsApp notification: {e}")

    # Dispatch async invoice generation + email to customer
    # Only generate invoice immediately for COD orders (paid on delivery).
    # For online payments (paymob, kashier), invoice is generated after
    # payment confirmation via webhook.
    is_cod = not request.payment_method or request.payment_method == "cod"
    if customer_email and is_cod:
        try:
            import asyncio
            from uuid import uuid4

            from src.core.entities.invoice import (
                BuyerInfo,
                Invoice,
                InvoiceStatus,
                SellerInfo,
            )
            from src.infrastructure.external_services.resend.email_service import (
                ResendEmailService,
            )
            from src.infrastructure.repositories.invoice_repository import (
                InvoiceRepository,
            )

            # Capture all data needed before creating the background task
            _inv_store_id = store_id
            _inv_tenant_id = store.tenant_id
            _inv_order_id = created_order.id
            _inv_order_number = created_order.order_number
            _inv_customer_id = current_customer.id
            _inv_customer_email = customer_email
            _inv_customer_name = current_customer.full_name
            _inv_store_name = store.name
            _inv_store_logo_url = store.logo_url
            _inv_currency = currency
            _inv_language = store.default_language
            _inv_line_items = [
                {
                    "name": li.product_name,
                    "sku": li.sku,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                }
                for li in order_line_items
            ]
            _inv_ship_addr = {
                "first_name": ship_addr.first_name,
                "last_name": ship_addr.last_name,
                "address_line1": ship_addr.address_line1,
                "city": ship_addr.city,
                "phone": ship_addr.phone,
            }
            # Pull store address & ETA/invoice settings for proper seller info
            _inv_store_address = dict(store.address) if store.address else {}
            _inv_store_settings = dict(store.settings) if store.settings else {}

            async def _generate_invoice():
                try:
                    from src.infrastructure.database.connection import (
                        AsyncSessionLocal,
                    )
                    from src.infrastructure.external_services.eta.qr_generator import (
                        generate_eta_qr_code,
                    )

                    # Build seller from store address + settings (ETA fields)
                    seller = SellerInfo(
                        tax_id=_inv_store_settings.get("tax_id", ""),
                        name=_inv_store_name,
                        name_ar=_inv_store_settings.get("name_ar", _inv_store_name),
                        branch_id=_inv_store_settings.get("branch_id", "0"),
                        country=_inv_store_address.get("country", "EG"),
                        governorate=_inv_store_address.get(
                            "governorate", _inv_store_address.get("state", "")
                        ),
                        city=_inv_store_address.get("city", ""),
                        street=_inv_store_address.get(
                            "street", _inv_store_address.get("address_line1", "")
                        ),
                        building_number=_inv_store_address.get("building_number", ""),
                        activity_code=_inv_store_settings.get("activity_code", "4649"),
                    )
                    buyer_name = (
                        f"{_inv_ship_addr.get('first_name', '')} {_inv_ship_addr.get('last_name', '')}".strip()
                        or _inv_customer_name
                    )
                    buyer = BuyerInfo(
                        buyer_type="P",
                        name=buyer_name,
                        name_ar=buyer_name,
                        city=_inv_ship_addr.get("city", ""),
                        street=_inv_ship_addr.get("address_line1", ""),
                        phone=_inv_ship_addr.get("phone", ""),
                        email=_inv_customer_email,
                    )

                    # Create invoice in DB
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            repo = InvoiceRepository(session)
                            inv_number = await repo.get_next_invoice_number(
                                _inv_store_id
                            )

                            invoice = Invoice(
                                id=uuid4(),
                                store_id=_inv_store_id,
                                tenant_id=_inv_tenant_id,
                                order_id=_inv_order_id,
                                customer_id=_inv_customer_id,
                                invoice_number=inv_number,
                                internal_id=_inv_order_number,
                                status=InvoiceStatus.ACCEPTED,
                                seller=seller,
                                buyer=buyer,
                                currency=_inv_currency,
                            )

                            for item in _inv_line_items:
                                invoice.add_line_item(
                                    description=item["name"],
                                    description_ar=item["name"],
                                    item_code=item.get("sku") or "EG-0000-0000",
                                    quantity=Decimal(str(item["quantity"])),
                                    unit_price=Decimal(str(item["unit_price"])) / 100,
                                    internal_code=item.get("sku"),
                                )

                            # Generate ETA QR code
                            try:
                                qr_data, qr_image = generate_eta_qr_code(
                                    seller_name=seller.name_ar or seller.name,
                                    tax_number=seller.tax_id or "",
                                    invoice_date=invoice.date_issued,
                                    total_with_vat=invoice.total / 100,
                                    vat_amount=invoice.total_taxes / 100,
                                )
                                invoice.qr_code_data = qr_data
                                invoice.qr_code_image = qr_image
                            except Exception as qr_exc:
                                logger.warning(f"QR code generation failed: {qr_exc}")

                            # Simulate ETA acceptance
                            invoice.eta_uuid = f"simulated-{uuid4().hex[:12]}"
                            invoice.eta_long_id = f"simulated-long-{uuid4().hex[:20]}"
                            invoice.eta_status_code = "accepted"

                            created_inv = await repo.create(invoice)

                    # Generate PDF with store logo
                    pdf_bytes = await asyncio.to_thread(
                        _generate_invoice_pdf, created_inv, _inv_store_logo_url
                    )

                    # Send email with PDF
                    svc = ResendEmailService()
                    await svc.send_invoice_email(
                        email=_inv_customer_email,
                        order_number=_inv_order_number,
                        invoice_number=created_inv.invoice_number,
                        pdf_bytes=pdf_bytes,
                        store_name=_inv_store_name,
                        language=_inv_language,
                    )
                    logger.info(
                        f"Invoice {created_inv.invoice_number} generated and emailed "
                        f"to {_inv_customer_email}"
                    )
                except Exception as exc:
                    logger.warning(f"Invoice generation/email failed: {exc}")

            asyncio.create_task(_generate_invoice())
        except Exception as e:
            logger.warning(f"Failed to dispatch invoice generation: {e}")

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
    from src.api.v1.routes.storefront.cart import _carts

    _carts.pop(current_customer.id, None)

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
