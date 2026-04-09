"""Store settings routes."""

import base64
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from src.api.dependencies import (
    get_current_store,
    get_onboarding_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.settings import (
    BostaCredentialsResponse,
    CreateShippingZoneRequest,
    CustomizationFooter,
    CustomizationHeader,
    CustomizationHero,
    CustomizationIdentity,
    CustomizationLabels,
    CustomizationLayout,
    CustomizationNavigation,
    CustomizationNavLink,
    CustomizationProducts,
    CustomizationResponse,
    CustomizationSocialLinks,
    CustomizationTheme,
    InvoiceSettingsResponse,
    KashierCredentialsResponse,
    NotificationTemplate,
    PaymentMethodStatus,
    PaymentSettingsResponse,
    PaymobCredentialsResponse,
    SaveBostaCredentialsRequest,
    SaveKashierCredentialsRequest,
    SavePaymobCredentialsRequest,
    ShippingCarrierStatus,
    ShippingSettingsResponse,
    ShippingZone,
    StoreSettingsResponse,
    UpdateCustomizationRequest,
    UpdateInvoiceSettingsRequest,
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
    operation_id="get_all_settings",
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
    operation_id="get_payment_settings",
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
    operation_id="update_payment_settings",
)
async def update_payment_settings(
    request: UpdatePaymentSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
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


# ============ Paymob Credentials ============

logger = logging.getLogger(__name__)


@router.put(
    "/payment/paymob/credentials",
    response_model=SuccessResponse[PaymobCredentialsResponse],
    summary="Save Paymob credentials",
    operation_id="save_paymob_credentials",
)
async def save_paymob_credentials(
    request: SavePaymobCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save or update Paymob payment gateway credentials for the store.

    Credentials are encrypted at rest using AES-128 (Fernet).
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()

    credential_data = {
        "secret_key": request.secret_key,
        "public_key": request.public_key,
        "hmac_secret": request.hmac_secret,
        "card_integration_id": request.card_integration_id,
        "wallet_integration_id": request.wallet_integration_id,
    }

    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["paymob"] = {
        "enabled": True,
        "is_configured": True,
        "last_configured": datetime.now(UTC).isoformat(),
        "encrypted_credentials": encrypted_b64,
        "encryption_key_id": key_id,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )

    logger.info(f"Paymob credentials saved for store {store.id}")

    return SuccessResponse(
        data=PaymobCredentialsResponse(
            is_configured=True,
            public_key_masked=secrets.mask_credential(request.public_key),
            secret_key_masked=secrets.mask_credential(request.secret_key),
            hmac_secret_masked=secrets.mask_credential(request.hmac_secret),
            card_integration_id=request.card_integration_id,
            wallet_integration_id=request.wallet_integration_id,
            last_configured=payment_settings["paymob"]["last_configured"],
        ),
        message="Paymob credentials saved successfully",
    )


@router.get(
    "/payment/paymob/credentials",
    response_model=SuccessResponse[PaymobCredentialsResponse],
    summary="Get Paymob credentials status",
    operation_id="get_paymob_credentials",
)
async def get_paymob_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Paymob credential status for the store."""
    settings = store.settings or {}
    paymob_settings = settings.get("payment", {}).get("paymob", {})

    if not paymob_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=PaymobCredentialsResponse(is_configured=False),
            message="Paymob credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = paymob_settings["encryption_key_id"]
    encrypted = base64.b64decode(paymob_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt Paymob credentials for store {store.id}")
        return SuccessResponse(
            data=PaymobCredentialsResponse(
                is_configured=True,
                last_configured=paymob_settings.get("last_configured"),
            ),
            message="Credentials configured but could not be read. Please re-save.",
        )

    return SuccessResponse(
        data=PaymobCredentialsResponse(
            is_configured=True,
            public_key_masked=secrets.mask_credential(creds["public_key"]),
            secret_key_masked=secrets.mask_credential(creds["secret_key"]),
            hmac_secret_masked=secrets.mask_credential(creds["hmac_secret"]),
            card_integration_id=creds.get("card_integration_id"),
            wallet_integration_id=creds.get("wallet_integration_id"),
            last_configured=paymob_settings.get("last_configured"),
        ),
        message="Paymob credentials retrieved successfully",
    )


@router.delete(
    "/payment/paymob/credentials",
    response_model=SuccessResponse[PaymobCredentialsResponse],
    summary="Remove Paymob credentials",
    operation_id="delete_paymob_credentials",
)
async def delete_paymob_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove Paymob credentials and disable Paymob payments."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["paymob"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    logger.info(f"Paymob credentials removed for store {store.id}")

    return SuccessResponse(
        data=PaymobCredentialsResponse(is_configured=False),
        message="Paymob credentials removed successfully",
    )


# ============ Kashier Credentials ============


@router.put(
    "/payment/kashier/credentials",
    response_model=SuccessResponse[KashierCredentialsResponse],
    summary="Save Kashier credentials",
    operation_id="save_kashier_credentials",
)
async def save_kashier_credentials(
    request: SaveKashierCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save or update Kashier payment gateway credentials for the store."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()

    credential_data = {
        "merchant_id": request.merchant_id,
        "api_key": request.api_key,
        "secret_key": request.secret_key,
    }

    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["kashier"] = {
        "enabled": True,
        "is_configured": True,
        "last_configured": datetime.now(UTC).isoformat(),
        "encrypted_credentials": encrypted_b64,
        "encryption_key_id": key_id,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )

    logger.info(f"Kashier credentials saved for store {store.id}")

    return SuccessResponse(
        data=KashierCredentialsResponse(
            is_configured=True,
            merchant_id=request.merchant_id,
            api_key_masked=secrets.mask_credential(request.api_key),
            last_configured=payment_settings["kashier"]["last_configured"],
        ),
        message="Kashier credentials saved successfully",
    )


@router.get(
    "/payment/kashier/credentials",
    response_model=SuccessResponse[KashierCredentialsResponse],
    summary="Get Kashier credentials status",
    operation_id="get_kashier_credentials",
)
async def get_kashier_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Kashier credential status for the store."""
    settings = store.settings or {}
    kashier_settings = settings.get("payment", {}).get("kashier", {})

    if not kashier_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=KashierCredentialsResponse(is_configured=False),
            message="Kashier credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = kashier_settings["encryption_key_id"]
    encrypted = base64.b64decode(kashier_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt Kashier credentials for store {store.id}")
        return SuccessResponse(
            data=KashierCredentialsResponse(
                is_configured=True,
                last_configured=kashier_settings.get("last_configured"),
            ),
            message="Credentials configured but could not be read. Please re-save.",
        )

    return SuccessResponse(
        data=KashierCredentialsResponse(
            is_configured=True,
            merchant_id=creds["merchant_id"],
            api_key_masked=secrets.mask_credential(creds["api_key"]),
            last_configured=kashier_settings.get("last_configured"),
        ),
        message="Kashier credentials retrieved successfully",
    )


@router.delete(
    "/payment/kashier/credentials",
    response_model=SuccessResponse[KashierCredentialsResponse],
    summary="Remove Kashier credentials",
    operation_id="delete_kashier_credentials",
)
async def delete_kashier_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove Kashier credentials and disable Kashier payments."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["kashier"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    logger.info(f"Kashier credentials removed for store {store.id}")

    return SuccessResponse(
        data=KashierCredentialsResponse(is_configured=False),
        message="Kashier credentials removed successfully",
    )


# ============ Shipping Settings ============


@router.get(
    "/shipping",
    response_model=SuccessResponse[ShippingSettingsResponse],
    summary="Get shipping settings",
    operation_id="get_shipping_settings",
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
    operation_id="update_shipping_settings",
)
async def update_shipping_settings(
    request: UpdateShippingSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
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
    operation_id="add_shipping_zone",
)
async def add_shipping_zone(
    request: CreateShippingZoneRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
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
    operation_id="update_shipping_zone",
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
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete shipping zone",
    operation_id="delete_shipping_zone",
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

    return None


# ============ Bosta Shipping Credentials ============


@router.put(
    "/shipping/bosta/credentials",
    response_model=SuccessResponse[BostaCredentialsResponse],
    summary="Save Bosta credentials",
    operation_id="save_bosta_credentials",
)
async def save_bosta_credentials(
    request: SaveBostaCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save or update Bosta shipping credentials for the store.

    Credentials are encrypted at rest using AES-128 (Fernet).
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()

    credential_data = {
        "api_key": request.api_key,
        "business_id": request.business_id,
        "webhook_secret": request.webhook_secret,
    }

    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())

    shipping_settings["bosta"] = {
        "enabled": True,
        "is_configured": True,
        "last_configured": datetime.now(UTC).isoformat(),
        "encrypted_credentials": encrypted_b64,
        "encryption_key_id": key_id,
        "auto_create_shipment": request.auto_create_shipment,
    }

    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.ADD_SHIPPING
    )

    logger.info(f"Bosta credentials saved for store {store.id}")

    return SuccessResponse(
        data=BostaCredentialsResponse(
            is_configured=True,
            api_key_masked=secrets.mask_credential(request.api_key),
            business_id=request.business_id,
            auto_create_shipment=request.auto_create_shipment,
            last_configured=shipping_settings["bosta"]["last_configured"],
        ),
        message="Bosta credentials saved successfully",
    )


@router.get(
    "/shipping/bosta/credentials",
    response_model=SuccessResponse[BostaCredentialsResponse],
    summary="Get Bosta credentials status",
    operation_id="get_bosta_credentials",
)
async def get_bosta_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Bosta credential status for the store."""
    settings = store.settings or {}
    bosta_settings = settings.get("shipping", {}).get("bosta", {})

    if not bosta_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=BostaCredentialsResponse(is_configured=False),
            message="Bosta credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = bosta_settings["encryption_key_id"]
    encrypted = base64.b64decode(bosta_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt Bosta credentials for store {store.id}")
        return SuccessResponse(
            data=BostaCredentialsResponse(
                is_configured=True,
                last_configured=bosta_settings.get("last_configured"),
            ),
            message="Credentials configured but could not be read. Please re-save.",
        )

    return SuccessResponse(
        data=BostaCredentialsResponse(
            is_configured=True,
            api_key_masked=secrets.mask_credential(creds["api_key"]),
            business_id=creds["business_id"],
            auto_create_shipment=bosta_settings.get("auto_create_shipment", False),
            last_configured=bosta_settings.get("last_configured"),
        ),
        message="Bosta credentials retrieved successfully",
    )


@router.delete(
    "/shipping/bosta/credentials",
    response_model=SuccessResponse[BostaCredentialsResponse],
    summary="Remove Bosta credentials",
    operation_id="delete_bosta_credentials",
)
async def delete_bosta_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove Bosta credentials and disable Bosta shipping."""
    settings = store.settings or {}
    shipping_settings = settings.get("shipping", _get_default_shipping_settings())

    shipping_settings["bosta"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    logger.info(f"Bosta credentials removed for store {store.id}")

    return SuccessResponse(
        data=BostaCredentialsResponse(is_configured=False),
        message="Bosta credentials removed successfully",
    )


# ============ Invoice / Tax Settings ============


@router.get(
    "/invoice",
    response_model=SuccessResponse[InvoiceSettingsResponse],
    summary="Get invoice/tax settings",
    operation_id="get_invoice_settings",
)
async def get_invoice_settings(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get invoice and tax settings (ETA seller info)."""
    settings = store.settings or {}
    invoice = settings.get("invoice", {})
    # Also check legacy top-level keys for backwards compat
    address = store.address or {}

    return SuccessResponse(
        data=InvoiceSettingsResponse(
            tax_id=invoice.get("tax_id", settings.get("tax_id", "")),
            name_ar=invoice.get("name_ar", settings.get("name_ar", "")),
            branch_id=invoice.get("branch_id", settings.get("branch_id", "0")),
            activity_code=invoice.get(
                "activity_code", settings.get("activity_code", "4649")
            ),
            governorate=invoice.get(
                "governorate", address.get("governorate", address.get("state", ""))
            ),
            city=invoice.get("city", address.get("city", "")),
            street=invoice.get(
                "street", address.get("street", address.get("address_line1", ""))
            ),
            building_number=invoice.get(
                "building_number", address.get("building_number", "")
            ),
        ),
        message="Invoice settings retrieved successfully",
    )


@router.patch(
    "/invoice",
    response_model=SuccessResponse[InvoiceSettingsResponse],
    summary="Update invoice/tax settings",
    operation_id="update_invoice_settings",
)
async def update_invoice_settings(
    request: UpdateInvoiceSettingsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update invoice and tax settings (ETA seller info)."""
    settings = store.settings or {}
    invoice = settings.get("invoice", {})

    # Migrate legacy top-level keys on first save
    if not invoice:
        invoice = {
            "tax_id": settings.get("tax_id", ""),
            "name_ar": settings.get("name_ar", ""),
            "branch_id": settings.get("branch_id", "0"),
            "activity_code": settings.get("activity_code", "4649"),
        }
        address = store.address or {}
        invoice["governorate"] = address.get("governorate", address.get("state", ""))
        invoice["city"] = address.get("city", "")
        invoice["street"] = address.get("street", address.get("address_line1", ""))
        invoice["building_number"] = address.get("building_number", "")

    # Update provided fields
    if request.tax_id is not None:
        invoice["tax_id"] = request.tax_id
    if request.name_ar is not None:
        invoice["name_ar"] = request.name_ar
    if request.branch_id is not None:
        invoice["branch_id"] = request.branch_id
    if request.activity_code is not None:
        invoice["activity_code"] = request.activity_code
    if request.governorate is not None:
        invoice["governorate"] = request.governorate
    if request.city is not None:
        invoice["city"] = request.city
    if request.street is not None:
        invoice["street"] = request.street
    if request.building_number is not None:
        invoice["building_number"] = request.building_number

    # Also write to top-level settings keys for checkout backwards compat
    settings["invoice"] = invoice
    settings["tax_id"] = invoice["tax_id"]
    settings["name_ar"] = invoice["name_ar"]
    settings["branch_id"] = invoice["branch_id"]
    settings["activity_code"] = invoice["activity_code"]
    store.settings = settings

    # Update address fields too
    address = store.address or {}
    address["governorate"] = invoice["governorate"]
    address["city"] = invoice["city"]
    address["street"] = invoice["street"]
    address["building_number"] = invoice["building_number"]
    store.address = address

    await store_repo.update(store)

    return SuccessResponse(
        data=InvoiceSettingsResponse(**invoice),
        message="Invoice settings updated successfully",
    )


# ============ WhatsApp Settings ============


@router.get(
    "/whatsapp",
    response_model=SuccessResponse[WhatsAppSettingsResponse],
    summary="Get WhatsApp settings",
    operation_id="get_whatsapp_settings",
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
    operation_id="update_whatsapp_settings",
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
            "accent_color": "",
            "background_color": "",
            "text_color": "",
            "button_style": "rounded",
            "enable_animations": True,
            "border_radius": 12,
            "heading_font": "Cairo",
            "nav_style": "floating",
        },
        "header": {
            "nav_layout": "left-aligned",
            "show_search_bar": True,
            "show_cart_icon": True,
            "announcement_text": "",
            "announcement_color": "#4318FF",
            "announcement_text_color": "#FFFFFF",
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


def _build_customization_response(
    settings: dict,
    theme_settings: dict | None = None,
) -> CustomizationResponse:
    """Build customization response from stored settings.

    ``settings`` is the per-store customization blob (lives in
    ``store.settings["customization"]``). ``theme_settings`` is the parallel
    JSONB column where the external theme metadata + merchant settings live;
    pass it to surface ``external_theme.merchant_settings`` in the response.
    """
    defaults = _get_default_customization()
    merged = _deep_merge(defaults, settings)

    footer_data = merged.get("footer", defaults["footer"])
    social_links_data = footer_data.get(
        "social_links", defaults["footer"]["social_links"]
    )

    # Build navigation
    nav_data = merged.get("navigation", {})
    raw_links = nav_data.get("links", [])
    nav_links = [CustomizationNavLink(**lnk) for lnk in raw_links] if raw_links else []

    # Build layout
    layout_data = merged.get("layout", {})

    # External theme merchant-edited settings (lives outside customization)
    external_theme_merchant_settings: dict[str, Any] | None = None
    if theme_settings:
        external_theme = theme_settings.get("external_theme")
        if isinstance(external_theme, dict):
            ms = external_theme.get("merchant_settings")
            if isinstance(ms, dict):
                external_theme_merchant_settings = ms

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
        navigation=CustomizationNavigation(
            links=nav_links,
            show_categories_in_nav=nav_data.get("show_categories_in_nav", True),
        ),
        labels=CustomizationLabels(**merged.get("labels", {})),
        layout=CustomizationLayout(**layout_data)
        if layout_data
        else CustomizationLayout(),
        is_published=merged.get("is_published", False),
        last_published_at=merged.get("last_published_at"),
        # V2 section engine fields
        schema_version=merged.get("schema_version"),
        templates=merged.get("templates"),
        external_theme_merchant_settings=external_theme_merchant_settings,
    )


@router.get(
    "/customization",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Get storefront customization settings",
    operation_id="get_customization",
)
async def get_customization(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get storefront customization settings for the store."""
    settings = store.settings or {}
    customization = settings.get("customization", {})

    return SuccessResponse(
        data=_build_customization_response(
            customization, theme_settings=store.theme_settings
        ),
        message="Customization settings retrieved successfully",
    )


@router.patch(
    "/customization",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Update storefront customization (save draft)",
    operation_id="update_customization",
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
    if request.navigation is not None:
        customization["navigation"] = {
            **customization.get("navigation", {}),
            **request.navigation,
        }
    if request.labels is not None:
        customization["labels"] = {
            **customization.get("labels", {}),
            **request.labels,
        }
    if request.layout is not None:
        customization["layout"] = {
            **customization.get("layout", {}),
            **request.layout,
        }

    # V2 section engine fields
    if request.schema_version is not None:
        if request.schema_version not in (1, 2):
            raise HTTPException(
                status_code=422,
                detail="schema_version must be 1 or 2",
            )
        customization["schema_version"] = request.schema_version
    if request.templates is not None:
        # Validate template structure
        for tpl_name, tpl_data in request.templates.items():
            if not isinstance(tpl_data, dict):
                raise HTTPException(
                    status_code=422,
                    detail=f"Template '{tpl_name}' must be an object",
                )
            if "sections" not in tpl_data or "order" not in tpl_data:
                raise HTTPException(
                    status_code=422,
                    detail=f"Template '{tpl_name}' must have 'sections' and 'order' keys",
                )
            if not isinstance(tpl_data["order"], list):
                raise HTTPException(
                    status_code=422,
                    detail=f"Template '{tpl_name}'.order must be an array",
                )
        existing_templates = customization.get("templates", {})
        customization["templates"] = {**existing_templates, **request.templates}

    # External theme merchant settings — persisted on the parallel
    # ``theme_settings`` JSONB column under ``external_theme.merchant_settings``
    # so the storefront's existing fetch path picks them up alongside
    # ``bundle_url`` / ``css_url`` / ``settings_schema``.
    theme_settings = store.theme_settings or {}
    if request.external_theme_merchant_settings is not None:
        external_theme = theme_settings.get("external_theme")
        if not isinstance(external_theme, dict):
            # Defensive: if no external theme is connected yet, store the
            # values anyway so they're not lost on a future connect.
            external_theme = {}
        existing_ms = external_theme.get("merchant_settings")
        if not isinstance(existing_ms, dict):
            existing_ms = {}
        external_theme["merchant_settings"] = {
            **existing_ms,
            **request.external_theme_merchant_settings,
        }
        theme_settings["external_theme"] = external_theme
        store.theme_settings = theme_settings

    # Save to store settings
    settings["customization"] = customization
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_customization_response(
            customization, theme_settings=store.theme_settings
        ),
        message="Customization saved as draft successfully",
    )


@router.post(
    "/customization/publish",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Publish storefront customization to live store",
    operation_id="publish_customization",
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
    published = _normalize_keys({
        "identity": customization.get("identity", {}),
        "theme": customization.get("theme", {}),
        "header": customization.get("header", {}),
        "hero": customization.get("hero", {}),
        "products": customization.get("products", {}),
        "footer": customization.get("footer", {}),
        "navigation": customization.get("navigation", {}),
        "labels": customization.get("labels", {}),
        "layout": customization.get("layout", {}),
    })

    # Include v2 section engine data if present (alongside v1 keys for compat)
    if customization.get("schema_version") == 2:
        published["schema_version"] = 2
        published["templates"] = customization.get("templates", {})

    # Preserve external theme metadata (bundle_url, css_url, settings_schema,
    # merchant_settings, …) across the publish — historically this handler
    # overwrote the whole theme_settings column and silently dropped it.
    existing_theme_settings = store.theme_settings or {}
    existing_external = existing_theme_settings.get("external_theme")
    if isinstance(existing_external, dict):
        published["external_theme"] = existing_external

    store.theme_settings = published

    settings["customization"] = customization
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_customization_response(
            customization, theme_settings=store.theme_settings
        ),
        message="Storefront published successfully",
    )


@router.post(
    "/customization/assets",
    response_model=SuccessResponse[dict],
    summary="Upload a customization asset (logo, favicon, hero image)",
    operation_id="upload_customization_asset",
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
    allowed_types = {"logo", "favicon", "hero_image", "profile_picture"}
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

    # Upload to configured storage (Cloudflare R2 / MinIO / local)
    from src.api.dependencies.services import get_storage_service
    from src.core.interfaces.services.storage_service import StorageBucket

    storage = get_storage_service()
    result = await storage.upload_file(
        file_content=content,
        filename=filename,
        content_type=file.content_type or "image/png",
        bucket=StorageBucket.STORES,
    )
    url = result.url

    return SuccessResponse(
        data={"url": url, "asset_type": asset_type, "filename": file.filename},
        message=f"{asset_type.replace('_', ' ').title()} uploaded successfully",
    )
