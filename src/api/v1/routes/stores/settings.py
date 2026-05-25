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
    get_storefront_cache_service,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.settings import (
    BostaCredentialsResponse,
    CodDepositPolicy,
    CodTrustResponse,
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
    FawaterakCredentialsResponse,
    InstapayCredentialsResponse,
    InvoiceSettingsResponse,
    KashierCredentialsResponse,
    NotificationTemplate,
    PaymentMethodStatus,
    PaymentSettingsResponse,
    PaymobCredentialsResponse,
    SaveBostaCredentialsRequest,
    SaveFawaterakCredentialsRequest,
    SaveInstapayCredentialsRequest,
    SaveKashierCredentialsRequest,
    SavePaymobCredentialsRequest,
    ShippingCarrierStatus,
    ShippingSettingsResponse,
    ShippingZone,
    StoreSettingsResponse,
    UpdateCodTrustRequest,
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
from src.infrastructure.cache import StorefrontCache
from src.infrastructure.repositories import OnboardingRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/settings")


def _get_default_payment_settings() -> dict:
    """Get default payment settings."""
    return {
        "cod": {"enabled": True, "is_configured": True, "last_configured": None},
        "fawry": {"enabled": False, "is_configured": False, "last_configured": None},
        "fawaterak": {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        },
        "paymob": {"enabled": False, "is_configured": False, "last_configured": None},
        "kashier": {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        },
        "instapay": {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        },
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

    # Deposit policy lives nested under cod. Extract into its own
    # top-level response field so the merchant UI can bind to it
    # without reaching into the `cod` object (which only carries
    # gateway-status booleans).
    cod_block = merged.get("cod", defaults["cod"]) or {}
    deposit_raw = cod_block.get("deposit_policy") or {}
    # Bypass the `_require_gateways_when_enabled` validator here —
    # reading pre-existing storage should never 500 if a merchant
    # saved a half-configured policy through an older code path. The
    # validator runs on writes via UpdatePaymentSettingsRequest.
    deposit_policy = CodDepositPolicy.model_construct(
        enabled=bool(deposit_raw.get("enabled", False)),
        amount_cents=int(deposit_raw.get("amount_cents", 0) or 0),
        ttl_minutes=int(deposit_raw.get("ttl_minutes", 30) or 30),
        auto_refund_on_cancel=bool(deposit_raw.get("auto_refund_on_cancel", False)),
        allowed_gateways=list(deposit_raw.get("allowed_gateways") or []),
    )

    def _status(key: str) -> PaymentMethodStatus:
        """Fallback to an empty status when a provider is missing from
        stored settings — happens on stores older than a given
        provider's introduction."""
        fallback = defaults.get(key, {"enabled": False, "is_configured": False})
        return PaymentMethodStatus(**merged.get(key, fallback))

    return PaymentSettingsResponse(
        cod=PaymentMethodStatus(**cod_block),
        fawry=_status("fawry"),
        fawaterak=_status("fawaterak"),
        paymob=_status("paymob"),
        kashier=_status("kashier"),
        instapay=_status("instapay"),
        vodafone_cash=_status("vodafone_cash"),
        bank_transfer=_status("bank_transfer"),
        bank_accounts_count=merged.get("bank_accounts_count", 0),
        cod_deposit_policy=deposit_policy,
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
    if getattr(request, "fawaterak_enabled", None) is not None:
        if not payment_settings.get("fawaterak", {}).get("is_configured"):
            raise HTTPException(
                status_code=400,
                detail="Fawaterak is not configured. Contact administrator.",
            )
        payment_settings.setdefault("fawaterak", {})["enabled"] = (
            request.fawaterak_enabled
        )
    if request.paymob_enabled is not None:
        if not payment_settings["paymob"]["is_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Paymob is not configured. Contact administrator.",
            )
        payment_settings["paymob"]["enabled"] = request.paymob_enabled
    if getattr(request, "kashier_enabled", None) is not None:
        # Kashier uses the tenant-credential system, so "is_configured"
        # here might be False even when the merchant has credentials
        # saved through that flow. We trust the toggle — the storefront's
        # /payment-methods endpoint re-checks credential availability.
        payment_settings.setdefault(
            "kashier",
            {"enabled": False, "is_configured": False, "last_configured": None},
        )["enabled"] = request.kashier_enabled
    if getattr(request, "instapay_enabled", None) is not None:
        if not payment_settings.get("instapay", {}).get("is_configured"):
            raise HTTPException(
                status_code=400,
                detail="InstaPay is not configured. Save your IPA first.",
            )
        payment_settings["instapay"]["enabled"] = request.instapay_enabled
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
    if request.cod_deposit_policy is not None:
        policy = request.cod_deposit_policy
        if policy.enabled:
            # Cross-field guard — every allowed gateway must actually
            # be enabled+configured on this store, otherwise the
            # deposit step would hit a gateway the customer can't use.
            not_ready: list[str] = []
            for provider in policy.allowed_gateways:
                cfg = payment_settings.get(provider, {})
                if not (cfg.get("enabled") and cfg.get("is_configured")):
                    not_ready.append(provider)
            if not_ready:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "These deposit gateways aren't enabled + configured: "
                        + ", ".join(not_ready)
                        + ". Configure them in Payment Setup first, or remove "
                        "them from the deposit policy."
                    ),
                )
        payment_settings.setdefault(
            "cod",
            {"enabled": True, "is_configured": True, "last_configured": None},
        )["deposit_policy"] = {
            "enabled": policy.enabled,
            "amount_cents": policy.amount_cents,
            "ttl_minutes": policy.ttl_minutes,
            "auto_refund_on_cancel": policy.auto_refund_on_cancel,
            "allowed_gateways": list(policy.allowed_gateways),
        }

    # Save settings
    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    # Auto-complete configure_payment onboarding step when any method is enabled
    any_enabled = any(
        payment_settings.get(m, {}).get("enabled", False)
        for m in (
            "cod",
            "fawry",
            "fawaterak",
            "paymob",
            "kashier",
            "instapay",
            "vodafone_cash",
            "bank_transfer",
        )
    )
    if any_enabled:
        await try_complete_onboarding_step(
            onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
        )

    return SuccessResponse(
        data=_build_payment_response(payment_settings),
        message="Payment settings updated successfully",
    )


# ============ COD Trust Network ============


_COD_TRUST_DEFAULTS = {
    "enabled": False,
    "threshold": 70,
    "min_confidence": "medium",
    "action": "block",
    "auto_rto_disabled": False,
    "auto_rto_days": 14,
}


def _get_cod_trust_settings(store_settings: dict | None) -> dict:
    """Read cod_trust block from store.settings, applying defaults."""
    raw = (store_settings or {}).get("cod_trust") or {}
    result = dict(_COD_TRUST_DEFAULTS)
    result.update({k: v for k, v in raw.items() if k in _COD_TRUST_DEFAULTS})
    return result


@router.get(
    "/cod-trust",
    response_model=SuccessResponse[CodTrustResponse],
    summary="Get COD trust network protection settings",
    operation_id="get_cod_trust_settings",
)
async def get_cod_trust_settings_endpoint(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get the COD trust network protection settings for the store."""
    cod_trust = _get_cod_trust_settings(store.settings)
    return SuccessResponse(
        data=CodTrustResponse(**cod_trust),
        message="COD trust settings retrieved",
    )


@router.patch(
    "/cod-trust",
    response_model=SuccessResponse[CodTrustResponse],
    summary="Update COD trust network protection settings",
    operation_id="update_cod_trust_settings",
)
async def update_cod_trust_settings_endpoint(
    request: UpdateCodTrustRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update the COD trust network protection settings for the store."""
    settings = dict(store.settings) if store.settings else {}
    cod_trust = _get_cod_trust_settings(settings)

    if request.enabled is not None:
        cod_trust["enabled"] = request.enabled
    if request.threshold is not None:
        cod_trust["threshold"] = request.threshold
    if request.min_confidence is not None:
        cod_trust["min_confidence"] = request.min_confidence
    if request.action is not None:
        cod_trust["action"] = request.action
    if request.auto_rto_disabled is not None:
        cod_trust["auto_rto_disabled"] = request.auto_rto_disabled
    if request.auto_rto_days is not None:
        cod_trust["auto_rto_days"] = request.auto_rto_days

    settings["cod_trust"] = cod_trust
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=CodTrustResponse(**cod_trust),
        message="COD trust settings updated",
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


# ============ Fawaterak Credentials ============


@router.put(
    "/payment/fawaterak/credentials",
    response_model=SuccessResponse[FawaterakCredentialsResponse],
    summary="Save Fawaterak credentials",
    operation_id="save_fawaterak_credentials",
)
async def save_fawaterak_credentials(
    request: SaveFawaterakCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save or update Fawaterak payment gateway credentials for the store."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()

    credential_data = {
        "api_key": request.api_key,
        "vendor_key": request.vendor_key,
        "environment": request.environment,
    }

    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["fawaterak"] = {
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

    logger.info(f"Fawaterak credentials saved for store {store.id}")

    return SuccessResponse(
        data=FawaterakCredentialsResponse(
            is_configured=True,
            api_key_masked=secrets.mask_credential(request.api_key),
            vendor_key_masked=secrets.mask_credential(request.vendor_key),
            environment=request.environment,
            last_configured=payment_settings["fawaterak"]["last_configured"],
        ),
        message="Fawaterak credentials saved successfully",
    )


@router.get(
    "/payment/fawaterak/credentials",
    response_model=SuccessResponse[FawaterakCredentialsResponse],
    summary="Get Fawaterak credentials status",
    operation_id="get_fawaterak_credentials",
)
async def get_fawaterak_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Fawaterak credential status for the store."""
    settings = store.settings or {}
    fawaterak_settings = settings.get("payment", {}).get("fawaterak", {})

    if not fawaterak_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=FawaterakCredentialsResponse(is_configured=False),
            message="Fawaterak credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = fawaterak_settings["encryption_key_id"]
    encrypted = base64.b64decode(fawaterak_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt Fawaterak credentials for store {store.id}")
        return SuccessResponse(
            data=FawaterakCredentialsResponse(
                is_configured=True,
                last_configured=fawaterak_settings.get("last_configured"),
            ),
            message="Credentials configured but could not be read. Please re-save.",
        )

    return SuccessResponse(
        data=FawaterakCredentialsResponse(
            is_configured=True,
            api_key_masked=secrets.mask_credential(creds["api_key"]),
            vendor_key_masked=secrets.mask_credential(creds["vendor_key"]),
            environment=creds.get("environment", "staging"),
            last_configured=fawaterak_settings.get("last_configured"),
        ),
        message="Fawaterak credentials retrieved successfully",
    )


@router.delete(
    "/payment/fawaterak/credentials",
    response_model=SuccessResponse[FawaterakCredentialsResponse],
    summary="Remove Fawaterak credentials",
    operation_id="delete_fawaterak_credentials",
)
async def delete_fawaterak_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove Fawaterak credentials and disable Fawaterak payments."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["fawaterak"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    logger.info(f"Fawaterak credentials removed for store {store.id}")

    return SuccessResponse(
        data=FawaterakCredentialsResponse(is_configured=False),
        message="Fawaterak credentials removed successfully",
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
        "identity": {
            "logo_url": "",
            "store_name": "",
            "favicon_url": "",
            "logo_footer_url": "",
            "logo_dark_url": "",
            "logo_alt_text": "",
            "logo_link_target": "/",
            "logo_width_desktop": 0,
            "logo_width_mobile": 0,
            "logo_footer_width_desktop": 0,
            "logo_footer_width_mobile": 0,
            "logo_padding": 0,
            "logo_background_color": "",
            "footer_logo_filter_mode": "none",
        },
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


def _normalize_theme_block(theme_block: Any) -> dict[str, Any]:
    """Coerce a ``theme_settings.theme`` slot to the canonical dict shape.

    Some stores were saved with ``theme`` as a plain string id
    (legacy form) rather than the canonical ``{"base_theme": "<id>",
    ...}`` object. Read paths used to do ``foo.get("theme", {}).get(
    "base_theme")`` and crashed with ``AttributeError: 'str' object
    has no attribute 'get'`` against the legacy rows. This wraps the
    string into the object shape so every consumer can assume a dict.
    """
    if isinstance(theme_block, str):
        return {"base_theme": theme_block}
    if isinstance(theme_block, dict):
        return theme_block
    return {}


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
        theme=CustomizationTheme(
            **_normalize_theme_block(merged.get("theme")) or defaults["theme"]
        ),
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
        # If the merchant is switching to a different base_theme, drop any
        # previously-saved section templates (hero/featured/promo text etc.).
        # Otherwise the V2 section engine would keep rendering the previous
        # theme's default copy — e.g. a luxury hero headline on a streetwear
        # theme — because templates are merchant-level overrides that persist
        # across theme switches.
        old_theme_block = _normalize_theme_block(customization.get("theme"))
        old_base_theme = old_theme_block.get("base_theme")
        new_base_theme = request.theme.get("base_theme")
        base_theme_changing = (
            new_base_theme is not None and new_base_theme != old_base_theme
        )
        customization["theme"] = {**old_theme_block, **request.theme}
        if base_theme_changing:
            customization.pop("templates", None)
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
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
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

    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )
    await cache.invalidate_theme(store.id)

    # Bust the Next.js storefront cache so merchant edits go live
    # immediately instead of waiting out the 60s per-store revalidate
    # window set on getStoreData().
    if store.subdomain:
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_customization_publish,
            )

            await revalidate_on_customization_publish(store.subdomain, str(store.id))
        except Exception:
            logger.warning(
                "Failed to revalidate storefront for %s after publish",
                store.subdomain,
                exc_info=True,
            )

    return SuccessResponse(
        data=_build_customization_response(
            customization, theme_settings=store.theme_settings
        ),
        message="Storefront published successfully",
    )


@router.post(
    "/customization/reset",
    response_model=SuccessResponse[CustomizationResponse],
    summary="Reset storefront customization to theme defaults",
    operation_id="reset_customization",
)
async def reset_customization(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Restore the merchant's customization to the fresh-store defaults.

    Wipes every customization section (identity, theme, header, hero,
    products, footer, navigation, labels, layout) plus the v2 section-
    engine templates. The currently selected ``theme.base_theme`` is
    preserved so the reset feels like "reset this theme" rather than
    "reset the store".

    Published customization (``store.theme_settings``) is untouched —
    merchants must click Publish to push the reset live.
    """
    settings = dict(store.settings or {})
    existing = settings.get("customization") or {}

    defaults = _get_default_customization()
    # Preserve current theme selection + any external-theme metadata
    # (bundle_url, css_url, merchant_settings on bring-your-own-theme).
    existing_theme = existing.get("theme") or {}
    if existing_theme.get("base_theme"):
        defaults["theme"]["base_theme"] = existing_theme["base_theme"]
    for key in ("bundle_url", "css_url", "settings_schema", "merchant_settings"):
        if key in existing_theme:
            defaults["theme"][key] = existing_theme[key]

    settings["customization"] = defaults
    store.settings = settings
    await store_repo.update(store)

    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )

    return SuccessResponse(
        data=_build_customization_response(
            defaults, theme_settings=store.theme_settings
        ),
        message="Customization reset to defaults",
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
    asset_type: str = Form(
        ...,
        description="Asset type: logo, favicon, hero_image, section_image, profile_picture, or social_image",
    ),
):
    """Upload an asset for storefront customization.

    Accepts logo, favicon, hero_image, section_image, profile_picture,
    or social_image (Open Graph) uploads, or `generic_file` for theme
    file_upload settings (PDFs, fonts, video, audio).
    Returns the URL of the uploaded asset.
    """
    # Image asset types share the strict image-only allowlist + 5MB cap.
    # `generic_file` is the catch-all for theme file_upload settings; we
    # broaden the content-type allowlist (PDF, fonts, video, audio) and
    # raise the cap to 10MB. Keeping these in one branch tree avoids
    # a parallel route — fewer places to keep auth + storage wiring in
    # sync.
    image_asset_types = {
        "logo",
        "favicon",
        "hero_image",
        "profile_picture",
        "section_image",
        "social_image",
    }
    generic_asset_type = "generic_file"
    allowed_types = image_asset_types | {generic_asset_type}
    if asset_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid asset_type. Must be one of: {', '.join(sorted(allowed_types))}",
        )

    image_content = {
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
        "image/x-icon",
        "image/gif",
    }
    # Generic-file allowlist: explicit application types we expect themes
    # to need + prefix-based whitelisting for fonts / video / audio
    # (where the subtype space is large and not worth enumerating).
    generic_content_exact = image_content | {
        "application/pdf",
        "application/zip",
        "application/x-font-ttf",
        "application/x-font-otf",
        "application/font-woff",
        "application/font-woff2",
        "application/octet-stream",  # some browsers send this for fonts
    }
    generic_content_prefixes = ("font/", "video/", "audio/")

    if asset_type == generic_asset_type:
        ct = (file.content_type or "").lower()
        if ct not in generic_content_exact and not ct.startswith(
            generic_content_prefixes
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid file type: {file.content_type}. "
                    f"Allowed: PDF, ZIP, fonts (TTF/OTF/WOFF/WOFF2), "
                    f"video, audio, or any image."
                ),
            )
    else:
        if file.content_type not in image_content:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.content_type}. Allowed: JPEG, PNG, SVG, WebP, ICO, GIF",
            )

    # Image assets get a 5MB cap (storefront images shouldn't be huge);
    # generic files get 10MB to fit fonts and small PDFs comfortably.
    max_size = 10 * 1024 * 1024 if asset_type == generic_asset_type else 5 * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {max_size // (1024 * 1024)}MB limit",
        )

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


@router.get(
    "/customization/assets",
    response_model=SuccessResponse[list],
    summary="List customization assets",
    operation_id="list_customization_assets",
)
async def list_customization_assets(
    store: Annotated[Store, Depends(get_current_store)],
):
    """List all uploaded theme/customization assets for this store."""
    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    prefix = f"customization/{store.id}/"

    try:
        assets = await storage.list_files(prefix)
        return SuccessResponse(data=assets, message="Assets retrieved successfully")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list assets: {str(e)}")


# ============ Checkout Fields Config ============

from src.core.checkout_fields import (  # noqa: E402
    SETTINGS_KEY as _CHECKOUT_KEY,
)
from src.core.checkout_fields import (
    CheckoutFieldsConfig,
)
from src.core.checkout_fields import (
    resolve_config as _resolve_checkout_config,
)


@router.get(
    "/checkout-fields",
    response_model=SuccessResponse[dict],
    summary="Get checkout fields config",
    operation_id="get_checkout_fields",
)
async def get_checkout_fields(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Return the merchant's checkout-fields config (with defaults merged)."""
    cfg = _resolve_checkout_config(store.settings)
    return SuccessResponse(data=cfg, message="Checkout fields retrieved")


@router.put(
    "/checkout-fields",
    response_model=SuccessResponse[dict],
    summary="Update checkout fields config",
    operation_id="update_checkout_fields",
)
async def update_checkout_fields(
    payload: CheckoutFieldsConfig,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Persist the merchant's checkout-fields config under ``settings.checkout_fields``."""
    settings = dict(store.settings or {})
    settings[_CHECKOUT_KEY] = payload.to_storage()
    store.settings = settings
    await store_repo.update(store)
    cfg = _resolve_checkout_config(settings)
    return SuccessResponse(data=cfg, message="Checkout fields updated")


# ============ InstaPay Credentials ============


@router.put(
    "/payment/instapay/credentials",
    response_model=SuccessResponse[InstapayCredentialsResponse],
    summary="Save InstaPay credentials",
    operation_id="save_instapay_credentials",
)
async def save_instapay_credentials(
    request: SaveInstapayCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save InstaPay configuration for the store.

    The IPA + fallback phone are encrypted at rest; the auto-approval
    thresholds sit alongside the encrypted blob in plaintext because
    they're policy knobs the merchant sees in the dashboard, not
    secrets.

    `request.ipa` and `request.fallback_phone` are both optional —
    when omitted, the existing encrypted blob is decrypted and those
    values carry forward. This lets the merchant edit display name,
    thresholds, or toggle enabled without re-typing the IPA (the UI
    shows it masked; it can never unmask to its true form).
    First-time saves must include `ipa`.
    """
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()

    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())
    existing = payment_settings.get("instapay") or {}

    # Determine the IPA + fallback phone to persist. On update, missing
    # fields carry forward from the previously-encrypted blob so the
    # UI can do partial updates.
    ipa_to_save: str | None = request.ipa
    phone_to_save: str | None = request.fallback_phone

    needs_existing = ipa_to_save is None or phone_to_save is None
    if needs_existing:
        if not existing.get("encrypted_credentials"):
            # First-time configuration — ipa must be supplied.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=("InstaPay address (IPA) is required for the first save."),
            )
        try:
            prev_key_id = existing["encryption_key_id"]
            prev_encrypted = base64.b64decode(existing["encrypted_credentials"])
            prev_creds = await secrets.decrypt(prev_encrypted, prev_key_id)
        except Exception:
            # If the prior blob can't be decrypted (key rotation issue,
            # corrupt bytes), force the merchant to supply a fresh IPA
            # rather than silently corrupting the record.
            logger.error(
                "Failed to decrypt existing InstaPay credentials for "
                f"store {store.id} during partial update"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Could not read existing InstaPay credentials. Please "
                    "re-enter your IPA to save."
                ),
            )
        if ipa_to_save is None:
            ipa_to_save = prev_creds.get("ipa")
        if phone_to_save is None:
            phone_to_save = prev_creds.get("fallback_phone")

    if not ipa_to_save:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="InstaPay address (IPA) is required.",
        )

    key_id = await secrets.get_current_key_id()
    credential_data = {
        "ipa": ipa_to_save,
        "fallback_phone": phone_to_save,
    }
    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    # qr_link_url: ``None`` from the request means "leave alone";
    # empty string means "clear"; any non-empty value overwrites.
    if request.qr_link_url is None:
        qr_link_to_save = existing.get("qr_link_url")
    else:
        qr_link_to_save = request.qr_link_url.strip() or None

    payment_settings["instapay"] = {
        # Preserve the current enabled state on credential updates —
        # merchants editing thresholds shouldn't accidentally unmute
        # a disabled InstaPay option at checkout. First-time saves
        # still fall through to True via the `or True` below (new
        # credentials are worth enabling by default).
        "enabled": bool(existing.get("enabled", True)) if existing else True,
        "is_configured": True,
        "last_configured": datetime.now(UTC).isoformat(),
        "encrypted_credentials": encrypted_b64,
        "encryption_key_id": key_id,
        "ipa_display_name": request.ipa_display_name,
        "auto_approve_threshold_cents": request.auto_approve_threshold_cents,
        "auto_approve_daily_cap_cents": request.auto_approve_daily_cap_cents,
        "auto_approve_daily_count": request.auto_approve_daily_count,
        # The QR image is uploaded via a dedicated endpoint, so a
        # credentials PUT must not erase a previously-uploaded URL.
        "qr_image_url": existing.get("qr_image_url"),
        "qr_link_url": qr_link_to_save,
        # Phase C — merchant-facing OCR opt-in flags. The provider
        # itself is admin-managed and intentionally NOT read from the
        # request, so a merchant can't self-promote onto a paid tier.
        "ocr_provider": existing.get("ocr_provider"),
        "require_ocr_amount_match": request.require_ocr_amount_match,
        "require_ocr_ipa_match": request.require_ocr_ipa_match,
        "ocr_amount_tolerance_bps": request.ocr_amount_tolerance_bps,
        # Phase C extras
        "require_note_contains_reference": (request.require_note_contains_reference),
        "require_transaction_ref_match": (request.require_transaction_ref_match),
        "require_recipient_name_match": (request.require_recipient_name_match),
        "recipient_name_token": (
            request.recipient_name_token.strip()
            if request.recipient_name_token
            else None
        ),
    }

    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )

    logger.info(f"InstaPay credentials saved for store {store.id}")

    return SuccessResponse(
        data=InstapayCredentialsResponse(
            is_configured=True,
            enabled=bool(payment_settings["instapay"]["enabled"]),
            ipa_masked=secrets.mask_credential(ipa_to_save),
            ipa_display_name=request.ipa_display_name,
            fallback_phone=phone_to_save,
            auto_approve_threshold_cents=request.auto_approve_threshold_cents,
            auto_approve_daily_cap_cents=request.auto_approve_daily_cap_cents,
            auto_approve_daily_count=request.auto_approve_daily_count,
            last_configured=payment_settings["instapay"]["last_configured"],
            qr_image_url=payment_settings["instapay"].get("qr_image_url"),
            qr_link_url=payment_settings["instapay"].get("qr_link_url"),
            ocr_provider=payment_settings["instapay"].get("ocr_provider"),
            require_ocr_amount_match=bool(
                payment_settings["instapay"].get("require_ocr_amount_match", False)
            ),
            require_ocr_ipa_match=bool(
                payment_settings["instapay"].get("require_ocr_ipa_match", False)
            ),
            ocr_amount_tolerance_bps=int(
                payment_settings["instapay"].get("ocr_amount_tolerance_bps", 100)
            ),
            require_note_contains_reference=bool(
                payment_settings["instapay"].get(
                    "require_note_contains_reference", False
                )
            ),
            require_transaction_ref_match=bool(
                payment_settings["instapay"].get("require_transaction_ref_match", False)
            ),
            require_recipient_name_match=bool(
                payment_settings["instapay"].get("require_recipient_name_match", False)
            ),
            recipient_name_token=payment_settings["instapay"].get(
                "recipient_name_token"
            ),
        ),
        message="InstaPay credentials saved successfully",
    )


@router.get(
    "/payment/instapay/credentials",
    response_model=SuccessResponse[InstapayCredentialsResponse],
    summary="Get InstaPay credentials status",
    operation_id="get_instapay_credentials",
)
async def get_instapay_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked InstaPay config status for the store."""
    store_settings = store.settings or {}
    instapay_settings = store_settings.get("payment", {}).get("instapay", {})

    if not instapay_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=InstapayCredentialsResponse(is_configured=False),
            message="InstaPay credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = instapay_settings["encryption_key_id"]
    encrypted = base64.b64decode(instapay_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt InstaPay credentials for store {store.id}")
        return SuccessResponse(
            data=InstapayCredentialsResponse(
                is_configured=True,
                enabled=bool(instapay_settings.get("enabled")),
                last_configured=instapay_settings.get("last_configured"),
            ),
            message="InstaPay credentials configured but unreadable",
        )

    return SuccessResponse(
        data=InstapayCredentialsResponse(
            is_configured=True,
            enabled=bool(instapay_settings.get("enabled")),
            ipa_masked=secrets.mask_credential(creds.get("ipa", "")),
            ipa_display_name=instapay_settings.get("ipa_display_name"),
            fallback_phone=creds.get("fallback_phone"),
            auto_approve_threshold_cents=instapay_settings.get(
                "auto_approve_threshold_cents"
            ),
            auto_approve_daily_cap_cents=instapay_settings.get(
                "auto_approve_daily_cap_cents"
            ),
            auto_approve_daily_count=instapay_settings.get("auto_approve_daily_count"),
            last_configured=instapay_settings.get("last_configured"),
            qr_image_url=instapay_settings.get("qr_image_url"),
            qr_link_url=instapay_settings.get("qr_link_url"),
            ocr_provider=instapay_settings.get("ocr_provider"),
            require_ocr_amount_match=bool(
                instapay_settings.get("require_ocr_amount_match", False)
            ),
            require_ocr_ipa_match=bool(
                instapay_settings.get("require_ocr_ipa_match", False)
            ),
            ocr_amount_tolerance_bps=int(
                instapay_settings.get("ocr_amount_tolerance_bps", 100)
            ),
            require_note_contains_reference=bool(
                instapay_settings.get("require_note_contains_reference", False)
            ),
            require_transaction_ref_match=bool(
                instapay_settings.get("require_transaction_ref_match", False)
            ),
            require_recipient_name_match=bool(
                instapay_settings.get("require_recipient_name_match", False)
            ),
            recipient_name_token=instapay_settings.get("recipient_name_token"),
        )
    )


@router.delete(
    "/payment/instapay/credentials",
    response_model=SuccessResponse[InstapayCredentialsResponse],
    summary="Remove InstaPay credentials",
    operation_id="delete_instapay_credentials",
)
async def delete_instapay_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove the stored InstaPay IPA and disable InstaPay at checkout.

    Existing ``instapay_intents`` rows are not touched — they belong
    to already-placed orders and the merchant still needs to see /
    review their proofs. New orders can no longer choose InstaPay
    until credentials are re-saved.
    """
    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())

    payment_settings["instapay"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    logger.info(f"InstaPay credentials removed for store {store.id}")

    return SuccessResponse(
        data=InstapayCredentialsResponse(is_configured=False),
        message="InstaPay credentials removed successfully",
    )


# ============ InstaPay QR image (merchant-supplied) ============
#
# The InstaPay scheme's QR codes are EMVCo-encoded and only the
# official InstaPay app generates ones the app can scan back. We
# can't synthesise a valid QR client-side, so we let the merchant
# upload the static QR they generated inside their own InstaPay app
# and serve that image to checkout customers. The customer scans →
# InstaPay opens with the IPA prefilled → they type the amount + ref
# from the page into the note.


@router.post(
    "/payment/instapay/qr-image",
    response_model=SuccessResponse[InstapayCredentialsResponse],
    summary="Upload merchant-generated InstaPay QR image",
    operation_id="upload_instapay_qr_image",
)
async def upload_instapay_qr_image(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    file: Annotated[UploadFile, File(description="InstaPay QR image (PNG/JPG)")],
):
    """Persist a merchant-supplied InstaPay QR image.

    Stored in the same `STORES` bucket the customization assets use
    and the resulting URL is written into ``store.settings.payment.
    instapay.qr_image_url`` so checkout + the InstaPay payment page
    can render it.
    """
    allowed_content = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_content:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid file type: {file.content_type}. Allowed: JPEG, PNG, WebP."
            ),
        )

    max_size = 2 * 1024 * 1024  # 2 MB — QR screenshots are small
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=400, detail="File size exceeds 2MB limit.")

    ext = (
        file.filename.rsplit(".", 1)[-1]
        if file.filename and "." in file.filename
        else "png"
    )
    filename = f"instapay/{store.id}/qr_{uuid.uuid4().hex[:8]}.{ext}"

    from src.api.dependencies.services import get_storage_service
    from src.core.interfaces.services.storage_service import StorageBucket

    storage = get_storage_service()
    result = await storage.upload_file(
        file_content=content,
        filename=filename,
        content_type=file.content_type or "image/png",
        bucket=StorageBucket.STORES,
    )

    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())
    instapay_settings = payment_settings.get("instapay") or {}
    instapay_settings["qr_image_url"] = result.url
    payment_settings["instapay"] = instapay_settings
    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    logger.info(f"InstaPay QR image uploaded for store {store.id}")

    return SuccessResponse(
        data=InstapayCredentialsResponse(
            is_configured=bool(instapay_settings.get("is_configured")),
            enabled=bool(instapay_settings.get("enabled")),
            ipa_display_name=instapay_settings.get("ipa_display_name"),
            auto_approve_threshold_cents=instapay_settings.get(
                "auto_approve_threshold_cents"
            ),
            auto_approve_daily_cap_cents=instapay_settings.get(
                "auto_approve_daily_cap_cents"
            ),
            auto_approve_daily_count=instapay_settings.get("auto_approve_daily_count"),
            last_configured=instapay_settings.get("last_configured"),
            qr_image_url=result.url,
            qr_link_url=instapay_settings.get("qr_link_url"),
        ),
        message="InstaPay QR image uploaded successfully",
    )


@router.delete(
    "/payment/instapay/qr-image",
    response_model=SuccessResponse[InstapayCredentialsResponse],
    summary="Remove the uploaded InstaPay QR image",
    operation_id="delete_instapay_qr_image",
)
async def delete_instapay_qr_image(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Clear the uploaded QR image URL.

    Doesn't try to delete the underlying object — storage cleanup is
    best-effort and orphaned QRs cost ~10 KB. The field is what the
    storefront looks at; nulling it suppresses the QR section.
    """
    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())
    instapay_settings = payment_settings.get("instapay") or {}
    instapay_settings["qr_image_url"] = None
    payment_settings["instapay"] = instapay_settings
    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    return SuccessResponse(
        data=InstapayCredentialsResponse(
            is_configured=bool(instapay_settings.get("is_configured")),
            enabled=bool(instapay_settings.get("enabled")),
            ipa_display_name=instapay_settings.get("ipa_display_name"),
            auto_approve_threshold_cents=instapay_settings.get(
                "auto_approve_threshold_cents"
            ),
            auto_approve_daily_cap_cents=instapay_settings.get(
                "auto_approve_daily_cap_cents"
            ),
            auto_approve_daily_count=instapay_settings.get("auto_approve_daily_count"),
            last_configured=instapay_settings.get("last_configured"),
            qr_image_url=None,
            qr_link_url=instapay_settings.get("qr_link_url"),
        ),
        message="InstaPay QR image removed",
    )


# ============================================================================
# Meta Tracking (Pixel + Conversions API) — plan §13.2 / Wave 1C
# ============================================================================
#
# These endpoints back the merchant-hub "Marketing & Tracking → Meta" panel.
# Convention: PUT request preserves the existing CAPI access token when the
# body omits ``capi_access_token``; **422** when ``capi_enabled = true`` and
# no token is on file AND none is provided.
#
# The CAPI token is stored as a ``ServiceCredential`` row (encrypted via
# SecretsManager); ``store.settings.tracking.meta`` carries the public bits
# (pixel_id, flags, debug-mode expiry, domain-verification token).
# ============================================================================

import secrets as _stdlib_secrets  # noqa: E402 — alias avoids name clash
from datetime import timedelta  # noqa: E402

from sqlalchemy import select as _select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified as _flag_modified  # noqa: E402

from src.api.dependencies.database import get_db as _get_db  # noqa: E402
from src.api.v1.schemas.tenant.tracking import (  # noqa: E402
    MetaEventLogEntry,
    MetaTrackingResponse,
    MetaTrackingStatusResponse,
    SaveMetaTrackingRequest,
    SendMetaTestEventRequest,
    SendMetaTestEventResponse,
    TrackingSettingsResponse,
)
from src.application.services.meta_tracking_resolver import (  # noqa: E402
    resolve_mode,
)

_DEBUG_MODE_TTL_MINUTES = 60


def _meta_cfg(store: Store) -> dict:
    """Read the ``store.settings.tracking.meta`` sub-object (or empty)."""
    return ((store.settings or {}).get("tracking") or {}).get("meta") or {}


async def _has_active_capi_credential(db: _AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Check whether a META_CAPI ServiceCredential row exists + is active."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    q = (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.META_CAPI)
        .where(ServiceCredential.is_active.is_(True))
    )
    return (await db.execute(q)).scalar_one_or_none() is not None


async def _get_capi_credential(db: _AsyncSession, tenant_id: uuid.UUID):
    """Return the META_CAPI ``ServiceCredential`` row (active or not), or None."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    q = (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.META_CAPI)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _build_meta_response(
    db: _AsyncSession,
    store: Store,
) -> MetaTrackingResponse:
    """Compose the public settings shape from store + credential row."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    cfg = _meta_cfg(store)
    cred = await _get_capi_credential(db, store.tenant_id)
    has_token = cred is not None and cred.is_active
    mode = resolve_mode(cfg, has_token)

    masked = None
    if cred and cred.is_active:
        try:
            sm = get_secrets_manager()
            decrypted = await sm.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            raw = decrypted.get("access_token") or ""
            masked = sm.mask_credential(raw) if raw else None
        except Exception:
            logger.warning(
                "meta_capi_token_decrypt_failed_for_mask store_id=%s",
                store.id,
            )

    # Status: connected / configured_no_events / failing / disabled.
    status_label: str = "disabled"
    if mode != "off":
        from src.infrastructure.repositories.meta_event_log_repository import (
            MetaEventLogRepository,
        )

        log_repo = MetaEventLogRepository(db)
        recent = await log_repo.recent_for_store(store.id, limit=20)
        if not recent:
            status_label = "configured_no_events"
        else:
            last_5_failed = sum(
                1
                for r in recent[:5]
                if r.response_status is None or r.response_status >= 400
            ) == min(5, len(recent[:5]))
            if last_5_failed and len(recent) >= 5:
                status_label = "failing"
            else:
                status_label = "connected"

    debug_expires_at = cfg.get("debug_mode_expires_at")
    debug_expires_dt = None
    if debug_expires_at:
        try:
            debug_expires_dt = datetime.fromisoformat(
                debug_expires_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            debug_expires_dt = None
    debug_active = bool(debug_expires_dt and debug_expires_dt > datetime.now(UTC))

    last_validated_dt = None
    if cred and cred.last_validated_at:
        last_validated_dt = cred.last_validated_at

    return MetaTrackingResponse(
        pixel_id=cfg.get("pixel_id"),
        pixel_enabled=bool(cfg.get("pixel_enabled", False)),
        capi_enabled=bool(cfg.get("capi_enabled", False)),
        mode=mode,
        capi_access_token_masked=masked,
        domain_verification_token=cfg.get("domain_verification_token"),
        test_event_code=cfg.get("test_event_code"),
        consent_required=bool(cfg.get("consent_required", False)),
        # Wave 2 Phase 12 — surface the timing config so the UI can
        # pre-populate. None (legacy) means "fire on payment webhook".
        purchase_trigger=cfg.get("purchase_trigger"),
        lead_trigger=cfg.get("lead_trigger"),
        # Wave 2 Phase 15 — surface WhatsApp Lead toggle.
        whatsapp_lead_enabled=bool(cfg.get("whatsapp_lead_enabled", False)),
        # Wave 2 Phase 13 — surface multi-pixel list (None = legacy single).
        pixels=cfg.get("pixels"),
        # Wave 3 Phase 18 — surface granular consent policy (None = legacy).
        consent_settings=cfg.get("consent_settings"),
        debug_mode=debug_active,
        debug_mode_expires_at=debug_expires_dt,
        last_validated_at=last_validated_dt,
        status=status_label,
    )


@router.get(
    "/tracking",
    response_model=SuccessResponse[TrackingSettingsResponse],
    summary="Get all tracking settings",
    operation_id="get_tracking_settings",
)
async def get_tracking_settings(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Return the per-channel tracking config — only Meta today."""
    meta = await _build_meta_response(db, store)
    return SuccessResponse(
        data=TrackingSettingsResponse(meta=meta),
        message="Tracking settings retrieved",
    )


@router.put(
    "/tracking/meta",
    response_model=SuccessResponse[MetaTrackingResponse],
    summary="Save Meta Pixel + CAPI settings",
    operation_id="save_meta_tracking",
)
async def save_meta_tracking(
    request: SaveMetaTrackingRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Upsert the per-store Meta tracking config and (optional) CAPI token.

    422 if ``capi_enabled = true`` and no token is on file AND none is
    supplied in the body — see plan §13.2.
    """
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    settings_dict: dict = store.settings or {}
    tracking = settings_dict.get("tracking") or {}
    meta_cfg = tracking.get("meta") or {}

    # ── Validation: capi_enabled requires a token ──────────────────────
    existing_cred = await _get_capi_credential(db, store.tenant_id)
    has_existing_active_token = existing_cred is not None and existing_cred.is_active
    if request.capi_enabled and not (
        request.capi_access_token or has_existing_active_token
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "capi_access_token is required when capi_enabled=true "
                "and no token is on file"
            ),
        )

    # ── Persist / update credential when a new token was supplied ─────
    if request.capi_access_token:
        sm = get_secrets_manager()
        key_id = await sm.get_current_key_id()
        encrypted = await sm.encrypt(
            {"access_token": request.capi_access_token},
            key_id,
        )

        if existing_cred:
            existing_cred.credentials_encrypted = encrypted
            existing_cred.encryption_key_id = key_id
            existing_cred.is_active = True
            existing_cred.is_validated = False
            existing_cred.extra_metadata = {"pixel_id": request.pixel_id}
        else:
            new_cred = ServiceCredential(
                tenant_id=store.tenant_id,
                service_type=ServiceType.TRACKING,
                service_name=ServiceName.META_CAPI,
                credentials_encrypted=encrypted,
                encryption_key_id=key_id,
                is_active=True,
                is_validated=False,
                extra_metadata={"pixel_id": request.pixel_id},
            )
            db.add(new_cred)
        await db.flush()

    # ── Update store.settings.tracking.meta in place ──────────────────
    domain_token = meta_cfg.get(
        "domain_verification_token"
    ) or _stdlib_secrets.token_urlsafe(24)

    # Debug-mode expiry math lives server-side (per scope §C).
    debug_expires_iso: str | None = None
    if request.debug_mode:
        debug_expires_iso = (
            datetime.now(UTC) + timedelta(minutes=_DEBUG_MODE_TTL_MINUTES)
        ).isoformat()

    # Wave 2 Phase 13 — Multi-pixel persistence. When the request carries
    # an explicit ``pixels`` list, persist it as-is. Auto-sync the
    # legacy top-level ``pixel_id`` / ``pixel_enabled`` / ``capi_enabled``
    # to ``pixels[0]`` so older readers (the resolver's legacy fallback,
    # storefront pre-Phase-13 bundles still in cache) keep working.
    new_pixels = [p.model_dump() for p in request.pixels] if request.pixels else None

    new_meta_cfg = {
        **meta_cfg,
        "pixel_id": request.pixel_id,
        "pixel_enabled": bool(request.pixel_enabled),
        "capi_enabled": bool(request.capi_enabled),
        "test_event_code": request.test_event_code,
        "consent_required": bool(request.consent_required),
        "domain_verification_token": domain_token,
        "debug_mode_expires_at": debug_expires_iso,
        # Wave 2 Phase 12 — COD-aware Purchase / Lead firing config.
        # None preserves legacy behavior (paymob/fawry webhooks remain
        # the sole Purchase source).
        "purchase_trigger": request.purchase_trigger,
        "lead_trigger": request.lead_trigger,
        # Wave 2 Phase 15 — fire Lead when COD customer confirms via
        # WhatsApp reply. Off by default — opt-in.
        "whatsapp_lead_enabled": bool(request.whatsapp_lead_enabled),
        # Wave 2 Phase 13 — store-level multi-pixel list. None when
        # the merchant is still on the legacy single-pixel path.
        "pixels": new_pixels,
        # Wave 3 Phase 18 — granular consent policy. None preserves
        # the legacy single-toggle behavior gated on consent_required.
        "consent_settings": (
            request.consent_settings.model_dump()
            if request.consent_settings is not None
            else None
        ),
    }
    tracking["meta"] = new_meta_cfg
    settings_dict["tracking"] = tracking
    store.settings = settings_dict
    # SQLAlchemy needs to know the JSONB blob mutated.
    if hasattr(store, "__class__") and "settings" in getattr(
        store.__class__, "__dict__", {}
    ):
        try:
            _flag_modified(store, "settings")
        except Exception:
            pass
    await store_repo.update(store)

    logger.info(
        "meta_tracking_saved store_id=%s pixel_enabled=%s capi_enabled=%s "
        "token_updated=%s debug_mode=%s",
        store.id,
        request.pixel_enabled,
        request.capi_enabled,
        bool(request.capi_access_token),
        request.debug_mode,
    )

    response = await _build_meta_response(db, store)
    return SuccessResponse(data=response, message="Meta tracking saved")


@router.delete(
    "/tracking/meta",
    response_model=SuccessResponse[MetaTrackingResponse],
    summary="Disconnect Meta tracking",
    operation_id="delete_meta_tracking",
)
async def delete_meta_tracking(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Disconnect: revoke server-side on Meta + soft-delete locally + audit.

    Flow:
      1. Decrypt the merchant's CAPI token (if one is on file).
      2. Best-effort call to Meta's ``DELETE /me/permissions`` so the
         merchant's Meta Business Settings → Apps page also shows the
         NUMU app as removed — keeps the two surfaces in sync.
      3. Turn off ``pixel_enabled`` + ``capi_enabled`` on the store
         settings JSONB.
      4. Soft-delete the ``service_credentials`` row (``is_active=False``)
         so the audit trail of "this token existed once" survives.
      5. Audit log under ``ADMIN_CONFIG_CHANGE`` so a later dispute
         ("who turned off our Meta integration?") has a trail.

    The Meta-side revoke is best-effort: if Meta is down or the token
    is already invalid, the local cleanup still proceeds. Once the
    merchant clicks Disconnect they expect NUMU to stop using their
    data regardless of what Meta's API does.

    The ``meta_event_log`` rows are intentionally retained — they're
    audit data that the merchant might still need to debug a past
    campaign or chase a fbtrace_id with Meta support.
    """
    from src.application.services.audit_service import AuditService, EventType
    from src.infrastructure.external_services.meta.oauth_client import (
        MetaOAuthClient,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    cred = await _get_capi_credential(db, store.tenant_id)
    revoke_attempted = False
    revoke_succeeded = False
    had_active_token = cred is not None and cred.is_active

    # Step 1+2: best-effort server-side revoke. Only attempt when we
    # actually have a valid token on file — no point hitting Meta with
    # an empty access_token. MetaOAuthClient.is_configured guards the
    # case where the NUMU Meta App env vars aren't set (App Review
    # still pending) — without the app credentials the revoke endpoint
    # would 4xx anyway.
    if had_active_token:
        try:
            sm = get_secrets_manager()
            decrypted = await sm.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            access_token = (decrypted or {}).get("access_token")
            client = MetaOAuthClient()
            if access_token and client.is_configured:
                revoke_attempted = True
                revoke_succeeded = await client.revoke_permissions(
                    access_token=access_token
                )
        except Exception:
            # Decryption / network failures must not block local
            # cleanup — log and proceed.
            logger.warning(
                "meta_revoke_pre_local_cleanup_failed",
                extra={"store_id": str(store.id)},
                exc_info=True,
            )

    # Step 3: turn off flags on store.settings.tracking.meta.
    settings_dict: dict = store.settings or {}
    tracking = settings_dict.get("tracking") or {}
    meta_cfg = tracking.get("meta") or {}
    meta_cfg["pixel_enabled"] = False
    meta_cfg["capi_enabled"] = False
    meta_cfg["debug_mode_expires_at"] = None
    tracking["meta"] = meta_cfg
    settings_dict["tracking"] = tracking
    store.settings = settings_dict
    try:
        _flag_modified(store, "settings")
    except Exception:
        pass
    await store_repo.update(store)

    # Step 4: soft-delete the credential row (preserves audit history).
    if cred is not None:
        cred.is_active = False
        await db.flush()

    # Step 5: audit log so disputes have a trail.
    try:
        await AuditService(db).log(
            event_type=EventType.ADMIN_CONFIG_CHANGE,
            action="meta_disconnect",
            resource_type="store_meta_integration",
            resource_id=str(store.id),
            store_id=store.id,
            tenant_id=store.tenant_id,
            new_value={
                "pixel_enabled": False,
                "capi_enabled": False,
                "had_active_token": had_active_token,
                "meta_revoke_attempted": revoke_attempted,
                "meta_revoke_succeeded": revoke_succeeded,
            },
        )
        await db.commit()
    except Exception:
        logger.warning(
            "meta_disconnect_audit_log_failed",
            extra={"store_id": str(store.id)},
            exc_info=True,
        )

    logger.info(
        "meta_tracking_disconnected store_id=%s revoke_attempted=%s revoke_succeeded=%s",
        store.id,
        revoke_attempted,
        revoke_succeeded,
    )

    response = await _build_meta_response(db, store)
    return SuccessResponse(data=response, message="Meta tracking disconnected")


@router.post(
    "/tracking/meta/test-event",
    response_model=SuccessResponse[SendMetaTestEventResponse],
    summary="Send a synthetic Purchase test event to Meta",
    operation_id="send_meta_test_event",
)
async def send_meta_test_event(
    request: SendMetaTestEventRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Fire a synthetic Purchase via the Celery fan-out task.

    Rejects with 422 when the resolved mode is ``off`` or ``pixel_only``
    (no CAPI to test). This is intentional — the test-event flow only
    makes sense for modes that have a CAPI fan-out path.
    """
    from src.infrastructure.messaging.tasks.meta_capi import meta_capi_send_event

    cfg = _meta_cfg(store)
    has_token = await _has_active_capi_credential(db, store.tenant_id)
    mode = resolve_mode(cfg, has_token)
    if mode in ("off", "pixel_only"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(f"Test events require CAPI to be enabled. Current mode: {mode}"),
        )

    pixel_id = cfg.get("pixel_id")
    if not pixel_id:  # defensive — resolve_mode would have returned 'off'
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="pixel_id is required to send a test event",
        )

    event_id = f"test-{uuid.uuid4()}"
    currency = (
        store.default_currency.value
        if hasattr(store.default_currency, "value")
        else str(store.default_currency)
    )

    # Meta CAPI rejects events with no user_data (error_subcode 2804050 —
    # "This event has no user information"). For the synthetic test event
    # we send a full set of plausible-but-fake identifiers — the Celery
    # worker calls ``hash_user_data()`` (src/infrastructure/external_services/
    # meta/hashing.py) on every PII key before POSTing, so we pass RAW
    # values here using NUMU's internal vocabulary (email/phone/first_name/
    # ...) — the worker normalizes + SHA-256s downstream. ``ip`` and
    # ``user_agent`` are passed verbatim per Meta's spec. Sending the full
    # set maximizes Event Match Quality (EMQ) score in Events Manager so
    # the test row reports a green badge instead of a low-quality warning.
    synthetic_user_data = {
        # Hashed PII — Meta accepts any string; worker SHA-256s before send.
        "email": f"numu-test-{store.id}@test.numueg.app",
        "phone": "+201000000000",
        "first_name": "Numu",
        "last_name": "Test",
        "city": "Cairo",
        "country_code": "EG",
        "zip": "11511",
        "customer_id": f"numu-test:{store.id}",
        # Raw — Meta wants these unhashed per spec.
        "ip": "127.0.0.1",
        "user_agent": "NUMU-Test-Event/1.0",
    }

    meta_capi_send_event.delay(
        store_id=str(store.id),
        pixel_id=pixel_id,
        event_name="Purchase",
        event_id=event_id,
        event_time=int(datetime.now(UTC).timestamp()),
        event_source_url=None,
        user_data=synthetic_user_data,
        custom_data={
            "value": 0.01,
            "currency": currency,
            "order_id": event_id,
        },
        test_event_code=request.test_event_code,
        action_source="website",
    )

    logger.info(
        "meta_capi_test_event_enqueued store_id=%s event_id=%s test_event_code=%s",
        store.id,
        event_id,
        request.test_event_code,
    )

    return SuccessResponse(
        data=SendMetaTestEventResponse(
            enqueued=True,
            test_event_code=request.test_event_code,
            queued_event_id=event_id,
        ),
        message="Test event enqueued",
    )


@router.get(
    "/tracking/meta/events",
    response_model=SuccessResponse[list[MetaEventLogEntry]],
    summary="Get recent Meta CAPI events",
    operation_id="get_meta_events",
)
async def get_meta_events(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
    limit: int = 20,
):
    """Return last N ``meta_event_log`` rows, redacted.

    The ``request_payload.user_data`` sub-object is dropped entirely;
    only boolean indicators ("had_email": true) survive.
    """
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )

    limit = min(max(limit, 1), 100)
    repo = MetaEventLogRepository(db)
    rows = await repo.recent_for_store(store.id, limit=limit)

    out: list[MetaEventLogEntry] = []
    for r in rows:
        # Redact the request payload — drop user_data entirely; replace
        # it with hashed-presence indicators.
        redacted = dict(r.request_payload or {})
        ud = redacted.pop("user_data", None) or {}
        redacted["user_data_indicators"] = {
            "had_email": bool(ud.get("em")),
            "had_phone": bool(ud.get("ph")),
            "had_first_name": bool(ud.get("fn")),
            "had_last_name": bool(ud.get("ln")),
            "had_city": bool(ud.get("ct")),
            "had_country": bool(ud.get("country")),
            "had_zip": bool(ud.get("zp")),
            "had_external_id": bool(ud.get("external_id")),
            "had_fbp": bool(ud.get("fbp")),
            "had_fbc": bool(ud.get("fbc")),
        }
        out.append(
            MetaEventLogEntry(
                id=str(r.id),
                event_id=r.event_id,
                event_name=r.event_name,
                event_time=r.event_time,
                pixel_id=r.pixel_id,
                response_status=r.response_status,
                fbtrace_id=r.fbtrace_id,
                attempt_count=r.attempt_count,
                last_error=r.last_error,
                sent_at=r.sent_at,
                created_at=r.created_at,
                channel="server",
                request_payload_redacted=redacted,
            )
        )

    return SuccessResponse(data=out, message="Recent Meta events retrieved")


@router.get(
    "/tracking/meta/status",
    response_model=SuccessResponse[MetaTrackingStatusResponse],
    summary="Get Meta tracking status",
    operation_id="get_meta_tracking_status",
)
async def get_meta_tracking_status(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Live status badge data — see plan §7.5."""
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )

    cfg = _meta_cfg(store)
    cred = await _get_capi_credential(db, store.tenant_id)
    has_token = cred is not None and cred.is_active
    mode = resolve_mode(cfg, has_token)

    repo = MetaEventLogRepository(db)
    recent = await repo.recent_for_store(store.id, limit=20)
    failed = sum(
        1 for r in recent if r.response_status is None or r.response_status >= 400
    )
    failure_rate = (failed / len(recent)) if recent else 0.0

    if mode == "off":
        status_label = "disabled"
    elif not recent:
        status_label = "configured_no_events"
    elif (
        len(recent) >= 5
        and sum(
            1
            for r in recent[:5]
            if r.response_status is None or r.response_status >= 400
        )
        == 5
    ):
        status_label = "failing"
    else:
        status_label = "connected"

    return SuccessResponse(
        data=MetaTrackingStatusResponse(
            status=status_label,
            mode=mode,
            last_validated_at=cred.last_validated_at if cred else None,
            recent_failure_rate=round(failure_rate, 4),
            recent_event_count=len(recent),
        ),
        message="Meta tracking status retrieved",
    )
