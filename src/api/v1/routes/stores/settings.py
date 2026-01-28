"""Store settings routes."""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_current_store, get_store_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.settings import (
    CreateShippingZoneRequest,
    PaymentSettingsResponse,
    PaymentMethodStatus,
    ShippingCarrierStatus,
    ShippingSettingsResponse,
    ShippingZone,
    StoreSettingsResponse,
    UpdatePaymentSettingsRequest,
    UpdateShippingSettingsRequest,
    UpdateShippingZoneRequest,
    UpdateWhatsAppSettingsRequest,
    WhatsAppNotifications,
    WhatsAppSettingsResponse,
    NotificationTemplate,
)
from src.core.entities.store import Store
from src.infrastructure.repositories import StoreRepository

router = APIRouter(prefix="/{store_id}/settings")


def _get_default_payment_settings() -> dict:
    """Get default payment settings."""
    return {
        "cod": {"enabled": True, "is_configured": True, "last_configured": None},
        "fawry": {"enabled": False, "is_configured": False, "last_configured": None},
        "paymob": {"enabled": False, "is_configured": False, "last_configured": None},
        "vodafone_cash": {"enabled": False, "is_configured": False, "last_configured": None},
        "bank_transfer": {"enabled": False, "is_configured": False, "last_configured": None},
        "bank_accounts_count": 0,
    }


def _get_default_shipping_settings() -> dict:
    """Get default shipping settings."""
    return {
        "aramex": {"enabled": False, "is_configured": False, "last_configured": None},
        "bosta": {"enabled": False, "is_configured": False, "last_configured": None},
        "mylerz": {"enabled": False, "is_configured": False, "last_configured": None},
        "manual": {"enabled": True, "is_configured": True, "last_configured": None},
        "zones": [
            {"id": str(uuid.uuid4()), "zone": "Cairo & Giza", "governorates": "Cairo, Giza", "rate": 50, "estimated_days": "1-2 days"},
            {"id": str(uuid.uuid4()), "zone": "Alexandria", "governorates": "Alexandria", "rate": 60, "estimated_days": "2-3 days"},
            {"id": str(uuid.uuid4()), "zone": "Delta Region", "governorates": "Dakahlia, Gharbia, Monufia, Qalyubia", "rate": 70, "estimated_days": "3-4 days"},
            {"id": str(uuid.uuid4()), "zone": "Canal Cities", "governorates": "Port Said, Ismailia, Suez", "rate": 80, "estimated_days": "3-4 days"},
            {"id": str(uuid.uuid4()), "zone": "Upper Egypt", "governorates": "Assiut, Sohag, Qena, Luxor, Aswan", "rate": 100, "estimated_days": "4-6 days"},
        ],
        "free_shipping_threshold": 500,
    }


def _get_default_whatsapp_settings() -> dict:
    """Get default WhatsApp settings."""
    return {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
        "phone_number": None,
        "notifications": {
            "order_confirmation": {"enabled": True, "template": "مرحباً {{customerName}}، تم تأكيد طلبك رقم {{orderNumber}} بنجاح.", "delay": None},
            "order_shipped": {"enabled": True, "template": "تم شحن طلبك رقم {{orderNumber}}. رقم التتبع: {{trackingNumber}}", "delay": None},
            "order_delivered": {"enabled": True, "template": "تم توصيل طلبك رقم {{orderNumber}} بنجاح. شكراً لتسوقك معنا!", "delay": None},
            "abandoned_cart": {"enabled": True, "template": "لاحظنا أنك تركت منتجات في سلة التسوق. أكمل طلبك الآن!", "delay": 60},
            "low_stock": {"enabled": False, "template": "تنبيه: المنتج {{productName}} أوشك على النفاد. الكمية المتبقية: {{quantity}}", "delay": None},
        },
        "messages_today": 0,
        "delivery_rate": 0,
        "api_quota": 1000,
    }


def _build_payment_response(settings: dict) -> PaymentSettingsResponse:
    """Build payment settings response from stored settings."""
    defaults = _get_default_payment_settings()
    merged = {**defaults, **settings}

    return PaymentSettingsResponse(
        cod=PaymentMethodStatus(**merged.get("cod", defaults["cod"])),
        fawry=PaymentMethodStatus(**merged.get("fawry", defaults["fawry"])),
        paymob=PaymentMethodStatus(**merged.get("paymob", defaults["paymob"])),
        vodafone_cash=PaymentMethodStatus(**merged.get("vodafone_cash", defaults["vodafone_cash"])),
        bank_transfer=PaymentMethodStatus(**merged.get("bank_transfer", defaults["bank_transfer"])),
        bank_accounts_count=merged.get("bank_accounts_count", 0),
    )


def _build_shipping_response(settings: dict) -> ShippingSettingsResponse:
    """Build shipping settings response from stored settings."""
    defaults = _get_default_shipping_settings()
    merged = {**defaults, **settings}

    zones = [ShippingZone(**z) for z in merged.get("zones", defaults["zones"])]

    return ShippingSettingsResponse(
        aramex=ShippingCarrierStatus(**merged.get("aramex", defaults["aramex"])),
        bosta=ShippingCarrierStatus(**merged.get("bosta", defaults["bosta"])),
        mylerz=ShippingCarrierStatus(**merged.get("mylerz", defaults["mylerz"])),
        manual=ShippingCarrierStatus(**merged.get("manual", defaults["manual"])),
        zones=zones,
        free_shipping_threshold=merged.get("free_shipping_threshold", 500),
    )


def _build_whatsapp_response(settings: dict) -> WhatsAppSettingsResponse:
    """Build WhatsApp settings response from stored settings."""
    defaults = _get_default_whatsapp_settings()
    merged = {**defaults, **settings}

    notifications_data = merged.get("notifications", defaults["notifications"])
    notifications = WhatsAppNotifications(
        order_confirmation=NotificationTemplate(**notifications_data.get("order_confirmation", defaults["notifications"]["order_confirmation"])),
        order_shipped=NotificationTemplate(**notifications_data.get("order_shipped", defaults["notifications"]["order_shipped"])),
        order_delivered=NotificationTemplate(**notifications_data.get("order_delivered", defaults["notifications"]["order_delivered"])),
        abandoned_cart=NotificationTemplate(**notifications_data.get("abandoned_cart", defaults["notifications"]["abandoned_cart"])),
        low_stock=NotificationTemplate(**notifications_data.get("low_stock", defaults["notifications"]["low_stock"])),
    )

    return WhatsAppSettingsResponse(
        enabled=merged.get("enabled", False),
        is_configured=merged.get("is_configured", False),
        last_configured=merged.get("last_configured"),
        phone_number=merged.get("phone_number"),
        notifications=notifications,
        messages_today=merged.get("messages_today", 0),
        delivery_rate=merged.get("delivery_rate", 0),
        api_quota=merged.get("api_quota", 1000),
    )


# ============ All Settings ============

@router.get(
    "/",
    response_model=SuccessResponse[StoreSettingsResponse],
    summary="Get all store settings",
)
async def get_all_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get all settings for the store."""
    settings = store.settings or {}

    return SuccessResponse(
        data=StoreSettingsResponse(
            payment=_build_payment_response(settings.get("payment", {})),
            shipping=_build_shipping_response(settings.get("shipping", {})),
            whatsapp=_build_whatsapp_response(settings.get("whatsapp", {})),
        ),
        message="Settings retrieved successfully",
    )


# ============ Payment Settings ============

@router.get(
    "/payment",
    response_model=SuccessResponse[PaymentSettingsResponse],
    summary="Get payment settings",
)
async def get_payment_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get payment settings for the store."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", {})

    return SuccessResponse(
        data=_build_payment_response(payment_settings),
        message="Payment settings retrieved successfully",
    )


@router.patch(
    "/payment",
    response_model=SuccessResponse[PaymentSettingsResponse],
    summary="Update payment settings",
)
async def update_payment_settings(
    request: UpdatePaymentSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update payment settings for the store."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    # Update only provided fields
    if request.cod_enabled is not None:
        payment_settings["cod"]["enabled"] = request.cod_enabled
    if request.fawry_enabled is not None:
        if not payment_settings["fawry"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Fawry is not configured. Contact administrator.")
        payment_settings["fawry"]["enabled"] = request.fawry_enabled
    if request.paymob_enabled is not None:
        if not payment_settings["paymob"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Paymob is not configured. Contact administrator.")
        payment_settings["paymob"]["enabled"] = request.paymob_enabled
    if request.vodafone_cash_enabled is not None:
        if not payment_settings["vodafone_cash"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Vodafone Cash is not configured. Contact administrator.")
        payment_settings["vodafone_cash"]["enabled"] = request.vodafone_cash_enabled
    if request.bank_transfer_enabled is not None:
        if not payment_settings["bank_transfer"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Bank Transfer is not configured. Contact administrator.")
        payment_settings["bank_transfer"]["enabled"] = request.bank_transfer_enabled

    # Save settings
    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_payment_response(payment_settings),
        message="Payment settings updated successfully",
    )


# ============ Shipping Settings ============

@router.get(
    "/shipping",
    response_model=SuccessResponse[ShippingSettingsResponse],
    summary="Get shipping settings",
)
async def get_shipping_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get shipping settings for the store."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", {})

    return SuccessResponse(
        data=_build_shipping_response(shipping_settings),
        message="Shipping settings retrieved successfully",
    )


@router.patch(
    "/shipping",
    response_model=SuccessResponse[ShippingSettingsResponse],
    summary="Update shipping settings",
)
async def update_shipping_settings(
    request: UpdateShippingSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update shipping settings for the store."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())

    # Update only provided fields
    if request.aramex_enabled is not None:
        if not shipping_settings["aramex"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Aramex is not configured. Contact administrator.")
        shipping_settings["aramex"]["enabled"] = request.aramex_enabled
    if request.bosta_enabled is not None:
        if not shipping_settings["bosta"]["is_configured"]:
            raise HTTPException(status_code=400, detail="Bosta is not configured. Contact administrator.")
        shipping_settings["bosta"]["enabled"] = request.bosta_enabled
    if request.mylerz_enabled is not None:
        if not shipping_settings["mylerz"]["is_configured"]:
            raise HTTPException(status_code=400, detail="MylerZ is not configured. Contact administrator.")
        shipping_settings["mylerz"]["enabled"] = request.mylerz_enabled
    if request.manual_enabled is not None:
        shipping_settings["manual"]["enabled"] = request.manual_enabled
    if request.free_shipping_threshold is not None:
        shipping_settings["free_shipping_threshold"] = request.free_shipping_threshold

    # Save settings
    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_shipping_response(shipping_settings),
        message="Shipping settings updated successfully",
    )


@router.post(
    "/shipping/zones",
    response_model=SuccessResponse[ShippingZone],
    summary="Add shipping zone",
)
async def add_shipping_zone(
    request: CreateShippingZoneRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Add a new shipping zone."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())

    new_zone = {
        "id": str(uuid.uuid4()),
        "zone": request.zone,
        "governorates": request.governorates,
        "rate": request.rate,
        "estimated_days": request.estimated_days,
    }

    zones = shipping_settings.get("zones", [])
    zones.append(new_zone)
    shipping_settings["zones"] = zones

    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=ShippingZone(**new_zone),
        message="Shipping zone added successfully",
    )


@router.patch(
    "/shipping/zones/{zone_id}",
    response_model=SuccessResponse[ShippingZone],
    summary="Update shipping zone",
)
async def update_shipping_zone(
    zone_id: str,
    request: UpdateShippingZoneRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update a shipping zone."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())
    zones = shipping_settings.get("zones", [])

    zone_index = next((i for i, z in enumerate(zones) if z["id"] == zone_id), None)
    if zone_index is None:
        raise HTTPException(status_code=404, detail="Shipping zone not found")

    zone = zones[zone_index]
    if request.zone is not None:
        zone["zone"] = request.zone
    if request.governorates is not None:
        zone["governorates"] = request.governorates
    if request.rate is not None:
        zone["rate"] = request.rate
    if request.estimated_days is not None:
        zone["estimated_days"] = request.estimated_days

    zones[zone_index] = zone
    shipping_settings["zones"] = zones

    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=ShippingZone(**zone),
        message="Shipping zone updated successfully",
    )


@router.delete(
    "/shipping/zones/{zone_id}",
    response_model=SuccessResponse[dict],
    summary="Delete shipping zone",
)
async def delete_shipping_zone(
    zone_id: str,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete a shipping zone."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())
    zones = shipping_settings.get("zones", [])

    zone_index = next((i for i, z in enumerate(zones) if z["id"] == zone_id), None)
    if zone_index is None:
        raise HTTPException(status_code=404, detail="Shipping zone not found")

    zones.pop(zone_index)
    shipping_settings["zones"] = zones

    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data={"deleted": True, "id": zone_id},
        message="Shipping zone deleted successfully",
    )


# ============ WhatsApp Settings ============

@router.get(
    "/whatsapp",
    response_model=SuccessResponse[WhatsAppSettingsResponse],
    summary="Get WhatsApp settings",
)
async def get_whatsapp_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get WhatsApp settings for the store."""
    settings = store.settings or {}
    whatsapp_settings = settings.get("whatsapp", {})

    return SuccessResponse(
        data=_build_whatsapp_response(whatsapp_settings),
        message="WhatsApp settings retrieved successfully",
    )


@router.patch(
    "/whatsapp",
    response_model=SuccessResponse[WhatsAppSettingsResponse],
    summary="Update WhatsApp settings",
)
async def update_whatsapp_settings(
    request: UpdateWhatsAppSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update WhatsApp settings for the store."""
    settings = store.settings or {}
    whatsapp_settings = settings.get("whatsapp", _get_default_whatsapp_settings())

    # Update enabled status
    if request.enabled is not None:
        if not whatsapp_settings["is_configured"] and request.enabled:
            raise HTTPException(status_code=400, detail="WhatsApp is not configured. Contact administrator.")
        whatsapp_settings["enabled"] = request.enabled

    # Update notification templates
    if request.notifications is not None:
        for key, value in request.notifications.items():
            if key in whatsapp_settings["notifications"]:
                if isinstance(value, dict):
                    whatsapp_settings["notifications"][key].update(value)

    # Save settings
    settings["whatsapp"] = whatsapp_settings
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_whatsapp_response(whatsapp_settings),
        message="WhatsApp settings updated successfully",
    )
