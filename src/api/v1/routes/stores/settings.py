"""Store settings routes."""

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.dependencies import (
    get_current_store,
    get_onboarding_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.settings import (
    CreateShippingZoneRequest,
    CustomizationFooter,
    CustomizationHeader,
    CustomizationHero,
    CustomizationIdentity,
    CustomizationProducts,
    CustomizationResponse,
    CustomizationSocialLinks,
    CustomizationTheme,
    NotificationTemplate,
    PaymentMethodStatus,
    PaymentSettingsResponse,
    ShippingCarrierStatus,
    ShippingSettingsResponse,
    ShippingZone,
    StoreSettingsResponse,
    UpdateCustomizationRequest,
    UpdatePaymentSettingsRequest,
    UpdateShippingSettingsRequest,
    UpdateShippingZoneRequest,
    UpdateWhatsAppSettingsRequest,
    WhatsAppNotifications,
    WhatsAppSettingsResponse,
)
from src.application.use_cases.onboarding.auto_complete import (
    try_complete_onboarding_step,
)
from src.core.entities.onboarding import OnboardingStepKey
from src.core.entities.store import Store
from src.infrastructure.repositories import OnboardingRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/settings")


def _get_default_payment_settings() -> dict:
    """Get default payment settings."""
    return {
        "cod": {"enabled": True, "is_configured": True, "last_configured": None},
        "fawry": {"enabled": False, "is_configured": False, "last_configured": None},
        "paymob": {"enabled": False, "is_configured": False, "last_configured": None},
        "vodafone_cash": {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        },
        "bank_transfer": {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        },
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
            {
                "id": str(uuid.uuid4()),
                "zone": "Cairo & Giza",
                "governorates": "Cairo, Giza",
                "rate": 50,
                "estimated_days": "1-2 days",
            },
            {
                "id": str(uuid.uuid4()),
                "zone": "Alexandria",
                "governorates": "Alexandria",
                "rate": 60,
                "estimated_days": "2-3 days",
            },
            {
                "id": str(uuid.uuid4()),
                "zone": "Delta Region",
                "governorates": "Dakahlia, Gharbia, Monufia, Qalyubia",
                "rate": 70,
                "estimated_days": "3-4 days",
            },
            {
                "id": str(uuid.uuid4()),
                "zone": "Canal Cities",
                "governorates": "Port Said, Ismailia, Suez",
                "rate": 80,
                "estimated_days": "3-4 days",
            },
            {
                "id": str(uuid.uuid4()),
                "zone": "Upper Egypt",
                "governorates": "Assiut, Sohag, Qena, Luxor, Aswan",
                "rate": 100,
                "estimated_days": "4-6 days",
            },
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
            "order_confirmation": {
                "enabled": True,
                "template": "مرحباً {{customerName}}، تم تأكيد طلبك رقم {{orderNumber}} بنجاح.",
                "delay": None,
            },
            "order_shipped": {
                "enabled": True,
                "template": "تم شحن طلبك رقم {{orderNumber}}. رقم التتبع: {{trackingNumber}}",
                "delay": None,
            },
            "order_delivered": {
                "enabled": True,
                "template": "تم توصيل طلبك رقم {{orderNumber}} بنجاح. شكراً لتسوقك معنا!",
                "delay": None,
            },
            "abandoned_cart": {
                "enabled": True,
                "template": "لاحظنا أنك تركت منتجات في سلة التسوق. أكمل طلبك الآن!",
                "delay": 60,
            },
            "low_stock": {
                "enabled": False,
                "template": "تنبيه: المنتج {{productName}} أوشك على النفاد. الكمية المتبقية: {{quantity}}",
                "delay": None,
            },
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
        vodafone_cash=PaymentMethodStatus(
            **merged.get("vodafone_cash", defaults["vodafone_cash"])
        ),
        bank_transfer=PaymentMethodStatus(
            **merged.get("bank_transfer", defaults["bank_transfer"])
        ),
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
        order_confirmation=NotificationTemplate(
            **notifications_data.get(
                "order_confirmation", defaults["notifications"]["order_confirmation"]
            )
        ),
        order_shipped=NotificationTemplate(
            **notifications_data.get(
                "order_shipped", defaults["notifications"]["order_shipped"]
            )
        ),
        order_delivered=NotificationTemplate(
            **notifications_data.get(
                "order_delivered", defaults["notifications"]["order_delivered"]
            )
        ),
        abandoned_cart=NotificationTemplate(
            **notifications_data.get(
                "abandoned_cart", defaults["notifications"]["abandoned_cart"]
            )
        ),
        low_stock=NotificationTemplate(
            **notifications_data.get(
                "low_stock", defaults["notifications"]["low_stock"]
            )
        ),
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
    onboarding_repo: Annotated[OnboardingRepository, Depends(get_onboarding_repository)],
):
    """Update payment settings for the store."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    # Update only provided fields
    if request.cod_enabled is not None:
        payment_settings["cod"]["enabled"] = request.cod_enabled
    if request.fawry_enabled is not None:
        if not payment_settings["fawry"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Fawry is not configured. Contact administrator.",
            )
        payment_settings["fawry"]["enabled"] = request.fawry_enabled
    if request.paymob_enabled is not None:
        if not payment_settings["paymob"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Paymob is not configured. Contact administrator.",
            )
        payment_settings["paymob"]["enabled"] = request.paymob_enabled
    if request.vodafone_cash_enabled is not None:
        if not payment_settings["vodafone_cash"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Vodafone Cash is not configured. Contact administrator.",
            )
        payment_settings["vodafone_cash"]["enabled"] = request.vodafone_cash_enabled
    if request.bank_transfer_enabled is not None:
        if not payment_settings["bank_transfer"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Bank Transfer is not configured. Contact administrator.",
            )
        payment_settings["bank_transfer"]["enabled"] = request.bank_transfer_enabled

    # Save settings
    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    # Auto-complete configure_payment onboarding step when any method is enabled
    any_enabled = any(
        payment_settings.get(m, {}).get("enabled", False)
        for m in ("cod", "fawry", "paymob", "vodafone_cash", "bank_transfer")
    )
    if any_enabled:
        await try_complete_onboarding_step(
            onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
        )

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
    onboarding_repo: Annotated[OnboardingRepository, Depends(get_onboarding_repository)],
):
    """Update shipping settings for the store."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())

    # Update only provided fields
    if request.aramex_enabled is not None:
        if not shipping_settings["aramex"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Aramex is not configured. Contact administrator.",
            )
        shipping_settings["aramex"]["enabled"] = request.aramex_enabled
    if request.bosta_enabled is not None:
        if not shipping_settings["bosta"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Bosta is not configured. Contact administrator.",
            )
        shipping_settings["bosta"]["enabled"] = request.bosta_enabled
    if request.mylerz_enabled is not None:
        if not shipping_settings["mylerz"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="MylerZ is not configured. Contact administrator.",
            )
        shipping_settings["mylerz"]["enabled"] = request.mylerz_enabled
    if request.manual_enabled is not None:
        shipping_settings["manual"]["enabled"] = request.manual_enabled
    if request.free_shipping_threshold is not None:
        shipping_settings["free_shipping_threshold"] = request.free_shipping_threshold

    # Save settings
    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    # Auto-complete add_shipping onboarding step when any carrier is enabled
    any_enabled = any(
        shipping_settings.get(c, {}).get("enabled", False)
        for c in ("aramex", "bosta", "mylerz", "manual")
    )
    if any_enabled:
        await try_complete_onboarding_step(
            onboarding_repo, store.id, OnboardingStepKey.ADD_SHIPPING
        )

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
    onboarding_repo: Annotated[OnboardingRepository, Depends(get_onboarding_repository)],
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

    # Auto-complete add_shipping onboarding step when a zone is added
    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.ADD_SHIPPING
    )

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
            raise HTTPException(
                status_code=400,
                detail="WhatsApp is not configured. Contact administrator.",
            )
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


# ============ Storefront Customization ============


def _get_default_customization() -> dict:
    """Get default storefront customization settings."""
    return {
        "identity": {"logo_url": "", "store_name": "", "favicon_url": ""},
        "theme": {
            "base_theme": "modern",
            "primary_color": "",
            "secondary_color": "",
            "background_color": "",
            "text_color": "",
            "button_style": "rounded",
        },
        "header": {
            "nav_layout": "left-aligned",
            "show_search_bar": True,
            "show_cart_icon": True,
            "announcement_text": "",
            "announcement_color": "#4318FF",
        },
        "hero": {
            "hero_image_url": "",
            "headline": "",
            "subtitle": "",
            "cta_text": "",
            "cta_link": "",
        },
        "products": {
            "layout": "grid",
            "products_per_row": 3,
            "show_price": True,
            "show_rating": True,
        },
        "footer": {
            "footer_text": "",
            "social_links": {
                "facebook": "",
                "instagram": "",
                "twitter": "",
                "whatsapp": "",
            },
            "show_newsletter": True,
        },
        "is_published": False,
        "last_published_at": None,
    }


def _to_snake_case(name: str) -> str:
    """Convert camelCase to snake_case."""
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")


def _normalize_keys(obj: Any) -> Any:
    """Recursively convert all dict keys to snake_case and deduplicate."""
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            snake_key = _to_snake_case(key)
            # Later keys win, so snake_case originals override camelCase conversions
            result[snake_key] = _normalize_keys(value)
        return result
    if isinstance(obj, list):
        return [_normalize_keys(item) for item in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _build_customization_response(settings: dict) -> CustomizationResponse:
    """Build customization response from stored settings."""
    defaults = _get_default_customization()
    merged = _deep_merge(defaults, settings)

    footer_data = merged.get("footer", defaults["footer"])
    social_links_data = footer_data.get(
        "social_links", defaults["footer"]["social_links"]
    )

    return CustomizationResponse(
        customization_mode=merged.get("customization_mode", "preset"),
        identity=CustomizationIdentity(**merged.get("identity", defaults["identity"])),
        theme=CustomizationTheme(**merged.get("theme", defaults["theme"])),
        header=CustomizationHeader(**merged.get("header", defaults["header"])),
        hero=CustomizationHero(**merged.get("hero", defaults["hero"])),
        products=CustomizationProducts(**merged.get("products", defaults["products"])),
        footer=CustomizationFooter(
            footer_text=footer_data.get("footer_text", ""),
            social_links=CustomizationSocialLinks(**social_links_data),
            show_newsletter=footer_data.get("show_newsletter", True),
        ),
        is_published=merged.get("is_published", False),
        last_published_at=merged.get("last_published_at"),
    )


@router.get(
    "/customization",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Get storefront customization settings",
)
async def get_customization(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get storefront customization settings for the store."""
    settings = store.settings or {}
    customization = settings.get("customization", {})

    return SuccessResponse(
        data=_build_customization_response(customization),
        message="Customization settings retrieved successfully",
    )


@router.patch(
    "/customization",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Update storefront customization (save draft)",
)
async def update_customization(
    request: UpdateCustomizationRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Save storefront customization as draft. Does not publish to live store."""
    settings = store.settings or {}
    customization = _normalize_keys(
        settings.get("customization", _get_default_customization())
    )

    # Persist customization mode
    if request.customization_mode is not None:
        customization["customization_mode"] = request.customization_mode

    # Deep merge each section if provided (normalize existing to strip camelCase dupes)
    if request.identity is not None:
        customization["identity"] = {
            **customization.get("identity", {}),
            **request.identity,
        }
    if request.theme is not None:
        customization["theme"] = {**customization.get("theme", {}), **request.theme}
    if request.header is not None:
        customization["header"] = {**customization.get("header", {}), **request.header}
    if request.hero is not None:
        customization["hero"] = {**customization.get("hero", {}), **request.hero}
    if request.products is not None:
        customization["products"] = {
            **customization.get("products", {}),
            **request.products,
        }
    if request.footer is not None:
        footer_update = request.footer
        existing_footer = customization.get(
            "footer", _get_default_customization()["footer"]
        )
        # Handle nested social_links merge
        if "social_links" in footer_update and isinstance(
            footer_update["social_links"], dict
        ):
            existing_social = existing_footer.get("social_links", {})
            footer_update["social_links"] = {
                **existing_social,
                **footer_update["social_links"],
            }
        customization["footer"] = {**existing_footer, **footer_update}

    # Save to store settings
    settings["customization"] = customization
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_customization_response(customization),
        message="Customization saved as draft successfully",
    )


@router.post(
    "/customization/publish",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Publish storefront customization to live store",
)
async def publish_customization(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Publish the current customization draft to the live storefront."""
    settings = store.settings or {}
    customization = _normalize_keys(
        settings.get("customization", _get_default_customization())
    )

    # Mark as published with timestamp
    customization["is_published"] = True
    customization["last_published_at"] = datetime.now(UTC).isoformat()

    # Copy to theme_settings for the storefront to consume (normalized snake_case)
    store.theme_settings = _normalize_keys({
        "identity": customization.get("identity", {}),
        "theme": customization.get("theme", {}),
        "header": customization.get("header", {}),
        "hero": customization.get("hero", {}),
        "products": customization.get("products", {}),
        "footer": customization.get("footer", {}),
    })

    settings["customization"] = customization
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_customization_response(customization),
        message="Storefront published successfully",
    )


@router.post(
    "/customization/assets",
    response_model=SuccessResponse[dict],
    summary="Upload a customization asset (logo, favicon, hero image)",
)
async def upload_customization_asset(
    store: Annotated[Store, Depends(get_current_store)],
    file: UploadFile = File(...),
    asset_type: str = Form(..., description="Asset type: logo, favicon, or hero_image"),
):
    """Upload an asset for storefront customization.

    Accepts logo, favicon, or hero_image uploads.
    Returns the URL of the uploaded asset.
    """
    # Validate asset type
    allowed_types = {"logo", "favicon", "hero_image"}
    if asset_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid asset_type. Must be one of: {', '.join(allowed_types)}",
        )

    # Validate file type
    allowed_content = {
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
        "image/x-icon",
        "image/gif",
    }
    if file.content_type not in allowed_content:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {file.content_type}. Allowed: JPEG, PNG, SVG, WebP, ICO, GIF",
        )

    # Validate file size (max 5MB)
    max_size = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")

    # Generate a unique filename
    ext = (
        file.filename.rsplit(".", 1)[-1]
        if file.filename and "." in file.filename
        else "png"
    )
    filename = f"customization/{store.id}/{asset_type}_{uuid.uuid4().hex[:8]}.{ext}"

    # TODO: Upload to Cloudflare R2 or configured storage
    # For now, return a placeholder URL pattern
    # In production, use the CloudflareR2StorageService:
    #   from src.infrastructure.external_services.storage import get_storage_service
    #   storage = get_storage_service()
    #   url = await storage.upload(filename, content, file.content_type)
    url = f"/api/v1/assets/{filename}"

    return SuccessResponse(
        data={"url": url, "asset_type": asset_type, "filename": file.filename},
        message=f"{asset_type.replace('_', ' ').title()} uploaded successfully",
    )
