"""Public storefront upsell endpoint.

URL: /storefront/store/{store_id}/upsells
"""

import base64
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_product_repository, get_store_repository
from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_order_repository,
    get_upsell_rule_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.upsell import (
    AcceptUpsellRequest,
    AcceptUpsellResponse,
    UpsellOfferResponse,
)
from src.core.entities.customer import Customer
from src.infrastructure.database.models.tenant.saved_payment_method import (
    SavedPaymentMethodModel,
)
from src.infrastructure.repositories import (
    OrderRepository,
    ProductRepository,
    StoreRepository,
)
from src.infrastructure.repositories.upsell_rule_repository import (
    UpsellRuleRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_kashier_api_key(store_settings: dict) -> str | None:
    """Get Kashier API key from store.settings encrypted credentials."""
    kashier_settings = (store_settings or {}).get("payment", {}).get("kashier", {})
    if not kashier_settings.get("encrypted_credentials"):
        return None

    try:
        from src.infrastructure.external_services.secrets.secrets_manager import (
            get_secrets_manager,
        )

        secrets = get_secrets_manager()
        key_id = kashier_settings["encryption_key_id"]
        encrypted = base64.b64decode(kashier_settings["encrypted_credentials"])
        creds = await secrets.decrypt(encrypted, key_id)
        return creds.get("api_key")
    except Exception as e:
        logger.error(f"Failed to decrypt Kashier credentials: {e}")
        return None


@router.get(
    "/upsells",
    response_model=SuccessResponse[list[UpsellOfferResponse]],
    summary="Get matching upsell offers",
    operation_id="get_storefront_upsells",
)
async def get_upsell_offers(
    store_id: Annotated[UUID, Path(description="Store ID")],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_ids: str | None = Query(
        None, description="Comma-separated product UUIDs from the order"
    ),
    category_ids: str | None = Query(
        None, description="Comma-separated category UUIDs from order products"
    ),
    cart_value: int = Query(0, ge=0, description="Cart total in cents"),
    lang: str = Query("en", description="Language for headlines: en or ar"),
):
    """Get upsell offers matching the given order context.

    This is a public endpoint (no auth required) used on the order
    confirmation page to show post-purchase upsell offers.
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    # Parse IDs
    parsed_product_ids: list[UUID] = []
    if product_ids:
        for pid in product_ids.split(","):
            pid = pid.strip()
            if pid:
                try:
                    parsed_product_ids.append(UUID(pid))
                except ValueError:
                    pass

    parsed_category_ids: list[UUID] = []
    if category_ids:
        for cid in category_ids.split(","):
            cid = cid.strip()
            if cid:
                try:
                    parsed_category_ids.append(UUID(cid))
                except ValueError:
                    pass

    # Find matching rules
    rules = await upsell_repo.get_matching_rules(
        store_id=store_id,
        product_ids=parsed_product_ids,
        category_ids=parsed_category_ids,
        cart_value=cart_value,
    )

    # Build offers with product details
    offers: list[UpsellOfferResponse] = []
    for rule in rules:
        # Fetch the offer product
        product = await product_repo.get_by_id(rule.offer_product_id)
        if not product:
            continue

        # Skip out-of-stock products
        if product.quantity <= 0:
            continue

        original_price = product.price.cents

        # Calculate discounted price
        if rule.discount_type == "percentage":
            discount_amount = (original_price * rule.discount_value) // 100
            discounted_price = original_price - discount_amount
        elif rule.discount_type == "fixed":
            discounted_price = max(0, original_price - rule.discount_value)
        else:
            discounted_price = original_price

        compare_at = (
            product.compare_at_price.cents if product.compare_at_price else None
        )

        # Pick locale
        headline = rule.headline_ar if lang == "ar" else rule.headline_en
        description = rule.description_ar if lang == "ar" else rule.description_en

        offers.append(
            UpsellOfferResponse(
                rule_id=str(rule.id),
                product={
                    "id": str(product.id),
                    "name": product.name,
                    "slug": product.slug,
                    "price": original_price,
                    "compare_at_price": compare_at,
                    "images": product.images or [],
                    "is_in_stock": product.quantity > 0,
                },
                discount_type=rule.discount_type,
                discount_value=rule.discount_value,
                discounted_price=discounted_price,
                original_price=original_price,
                headline=headline,
                description=description,
            )
        )

    return SuccessResponse(
        data=offers,
        message="Upsell offers retrieved successfully",
    )


@router.post(
    "/upsells/accept",
    response_model=SuccessResponse[AcceptUpsellResponse],
    summary="Accept a post-purchase upsell offer",
    operation_id="accept_storefront_upsell",
)
async def accept_upsell_offer(
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: AcceptUpsellRequest,
    customer: Annotated[Customer, Depends(get_current_customer)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: AsyncSession = Depends(get_db),
):
    """Accept a post-purchase upsell offer and add it to the existing order.

    Requires customer auth — only the order owner can accept upsells.
    """
    # Validate order
    try:
        order_uuid = UUID(body.order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    order = await order_repo.get_by_id(order_uuid)
    if not order or order.store_id != store_id:
        raise HTTPException(status_code=404, detail="Order not found")

    # Verify this customer owns the order
    if order.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Not your order")

    # Validate rule
    try:
        rule_uuid = UUID(body.rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rule ID")

    rule = await upsell_repo.get_by_id(rule_uuid)
    if not rule or not rule.is_active or rule.store_id != store_id:
        raise HTTPException(status_code=404, detail="Upsell offer not found or expired")

    # Check max uses
    if rule.max_uses is not None and rule.uses_count >= rule.max_uses:
        raise HTTPException(status_code=410, detail="Upsell offer no longer available")

    # Fetch offer product
    product = await product_repo.get_by_id(rule.offer_product_id)
    if not product or product.quantity <= 0:
        raise HTTPException(status_code=410, detail="Product out of stock")

    # Calculate discounted price
    original_price = product.price.cents
    if rule.discount_type == "percentage":
        discount_amount = (original_price * rule.discount_value) // 100
        discounted_price = original_price - discount_amount
    elif rule.discount_type == "fixed":
        discounted_price = max(0, original_price - rule.discount_value)
    else:
        discounted_price = original_price

    # Add line item to the order
    new_line_item = {
        "product_id": str(product.id),
        "product_name": product.name,
        "quantity": 1,
        "unit_price": discounted_price,
        "total": discounted_price,
        "image_url": product.images[0] if product.images else None,
        "upsell_rule_id": str(rule.id),
    }

    current_items = list(order.line_items or [])
    current_items.append(new_line_item)
    order.line_items = current_items

    # Update order totals
    order.subtotal = (order.subtotal or 0) + discounted_price
    order.total = (order.total or 0) + discounted_price

    await order_repo.update(order)

    # ── Charge saved payment method if available ──
    saved_method_result = await db.execute(
        select(SavedPaymentMethodModel)
        .where(
            and_(
                SavedPaymentMethodModel.order_id == order.id,
                SavedPaymentMethodModel.is_active.is_(True),
            )
        )
        .order_by(SavedPaymentMethodModel.created_at.desc())
        .limit(1)
    )
    saved_method = saved_method_result.scalar_one_or_none()

    payment_charged = False
    if saved_method:
        store = await store_repo.get_by_id(store_id)
        if saved_method.gateway == "kashier" and store:
            api_key = await _get_kashier_api_key(store.settings)
            if api_key:
                from src.infrastructure.external_services.kashier import (
                    KashierPaymentService,
                )

                kashier = KashierPaymentService(api_key=api_key)
                result = await kashier.charge_saved_token(
                    card_token=saved_method.card_token,
                    amount=discounted_price,
                    currency=order.currency,
                    order_id=str(order.id),
                )
                payment_charged = result.success
                if not result.success:
                    logger.warning(
                        "upsell_token_charge_failed gateway=kashier error=%s",
                        result.error_message,
                    )

        elif saved_method.gateway == "paymob" and store:
            try:
                from src.infrastructure.external_services.paymob.payment_service import (
                    PaymobPaymentService,
                    get_merchant_paymob_credentials,
                )

                creds = await get_merchant_paymob_credentials(store.settings)
                paymob = PaymobPaymentService(
                    secret_key=creds["secret_key"],
                    card_integration_id=creds.get("card_integration_id"),
                )
                result = await paymob.charge_saved_token(
                    card_token=saved_method.card_token,
                    amount=discounted_price,
                    currency=order.currency,
                    order_id=str(order.id),
                )
                payment_charged = result.success
                if not result.success:
                    logger.warning(
                        "upsell_token_charge_failed gateway=paymob error=%s",
                        result.error_message,
                    )
            except Exception as e:
                logger.warning("upsell_paymob_charge_error error=%s", str(e))

    # Decrement product stock
    product.quantity = max(0, product.quantity - 1)

    # Increment rule usage count
    await upsell_repo.increment_uses(rule.id)

    logger.info(
        "Upsell accepted: order=%s rule=%s product=%s price=%d charged=%s",
        order.order_number,
        rule.id,
        product.name,
        discounted_price,
        payment_charged,
    )

    return SuccessResponse(
        data=AcceptUpsellResponse(
            order_id=str(order.id),
            product_name=product.name,
            discounted_price=discounted_price,
            new_total=order.total,
        ),
        message="Upsell added to order successfully",
    )
