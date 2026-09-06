"""Store settings routes."""

import base64
import contextlib
import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_store,
    get_onboarding_repository,
    get_product_repository,
    get_store_repository,
    get_storefront_cache_service,
)
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.settings import (
    BostaCredentialsResponse,
    CodAutopilotResponse,
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
    DeleteAssetRequest,
    FawaterakCredentialsResponse,
    InstapayCredentialsResponse,
    InvoiceSettingsResponse,
    KashierCredentialsResponse,
    MoyasarCredentialsResponse,
    NotificationTemplate,
    PaymentMethodStatus,
    PaymentSettingsResponse,
    PaymobCredentialsResponse,
    ProductLabelDef,
    ProductLabelsResponse,
    SaveBostaCredentialsRequest,
    SaveFawaterakCredentialsRequest,
    SaveInstapayCredentialsRequest,
    SaveKashierCredentialsRequest,
    SaveMoyasarCredentialsRequest,
    SavePaymobCredentialsRequest,
    SaveVodafoneCashCredentialsRequest,
    ShippingCarrierStatus,
    ShippingSettingsResponse,
    ShippingZone,
    StoreSettingsResponse,
    UpdateAssetMetaRequest,
    UpdateCodAutopilotRequest,
    UpdateCodTrustRequest,
    UpdateCustomizationRequest,
    UpdateInvoiceSettingsRequest,
    UpdatePaymentSettingsRequest,
    UpdateProductLabelsRequest,
    UpdateShippingSettingsRequest,
    UpdateShippingZoneRequest,
    UpdateWhatsAppSettingsRequest,
    VodafoneCashCredentialsResponse,
    WhatsAppNotifications,
    WhatsAppSettingsResponse,
)
from src.application.use_cases.onboarding.auto_complete import (
    try_complete_onboarding_step,
)
from src.core.entities.instapay import ManualPaymentMethod
from src.core.entities.onboarding import OnboardingStepKey
from src.core.entities.store import Store
from src.infrastructure.cache import StorefrontCache
from src.infrastructure.external_services.manual_transfer.merchant_config import (
    ManualConfigError,
    ManualConfigInput,
    build_config_block,
    cleared_config_block,
    read_config_view,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    default_auto_approve_enabled,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    human_name as manual_human_name,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    settings_key as manual_settings_key,
)
from src.infrastructure.repositories import (
    OnboardingRepository,
    ProductRepository,
    StoreRepository,
)

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


# Carriers that live in store settings but have no registry entry.
#
# ``aramex`` has a settings toggle and a hub card but no provider behind
# it. Kept so existing stores' stored values survive; see decision D2
# (likely answer: Aramex arrives via an aggregator, never as a native
# adapter). Do not add it to the registry until it has a provider.
#
# ``manual`` used to live here too. It is a real registry carrier as of
# P2 — see ``_CARRIER_DEFAULT_OVERRIDES`` for why its default survives.
_NON_REGISTRY_CARRIERS: dict[str, dict] = {
    "aramex": {"enabled": False, "is_configured": False, "last_configured": None},
}

# Carriers whose stored default is not "off".
#
# 🔴 ``manual`` ships enabled on every store, including both live ones.
# The registry loop below would otherwise generate `enabled: False` for
# it and silently switch manual shipping off for every existing merchant
# the next time their settings were defaulted. Do not remove this without
# a migration.
_CARRIER_DEFAULT_OVERRIDES: dict[str, dict] = {
    "manual": {"enabled": True, "is_configured": True, "last_configured": None},
}


def shipping_carrier_keys() -> list[str]:
    """Every carrier slug that can appear in a store's shipping settings.

    Registry carriers plus the non-registry ones above. Before this, four
    surfaces in this file hardcoded ``("aramex","bosta","mylerz","manual")``
    and **all four omitted J&T** — so a merchant could create J&T
    shipments through the shipments route but never enable J&T here.
    """
    from src.application.services.carrier_registry import carrier_slugs

    return [*carrier_slugs(), *_NON_REGISTRY_CARRIERS]


def _get_default_shipping_settings() -> dict:
    """Get default shipping settings.

    Carrier entries are generated from the registry, so adding a carrier
    there makes it configurable here with no change to this file.
    """
    from src.application.services.carrier_registry import carrier_slugs

    carriers: dict = {
        slug: dict(
            _CARRIER_DEFAULT_OVERRIDES.get(
                slug,
                {"enabled": False, "is_configured": False, "last_configured": None},
            )
        )
        for slug in carrier_slugs()
    }
    carriers.update({k: dict(v) for k, v in _NON_REGISTRY_CARRIERS.items()})

    return {
        **carriers,
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
        "restrict_to_zones": False,
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

    def _status(slug: str) -> ShippingCarrierStatus:
        raw = merged.get(slug) or defaults.get(slug) or {}
        return ShippingCarrierStatus(**{
            "enabled": bool(raw.get("enabled", False)),
            "is_configured": bool(raw.get("is_configured", False)),
            "last_configured": raw.get("last_configured"),
        })

    # `carriers` is the forward-looking shape: every carrier keyed by
    # slug, generated from the registry, so a new carrier appears without
    # touching this file. The four named fields below are kept for the
    # hub's current reads and go away once P3 consumes `carriers`.
    carriers = {slug: _status(slug) for slug in shipping_carrier_keys()}

    return ShippingSettingsResponse(
        carriers=carriers,
        aramex=carriers["aramex"],
        bosta=carriers["bosta"],
        mylerz=carriers["mylerz"],
        manual=carriers["manual"],
        zones=zones,
        free_shipping_threshold=merged.get("free_shipping_threshold", 500),
        restrict_to_zones=bool(merged.get("restrict_to_zones", False)),
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
    if getattr(request, "moyasar_enabled", None) is not None:
        if not payment_settings.get("moyasar", {}).get("is_configured"):
            raise HTTPException(
                status_code=400,
                detail="Moyasar is not configured. Contact administrator.",
            )
        payment_settings.setdefault("moyasar", {})["enabled"] = request.moyasar_enabled
    if request.vodafone_cash_enabled is not None:
        # "Contact administrator" was a leftover from when Vodafone Cash
        # was scaffolded as an API gateway needing a partnership. It is a
        # manual rail: the merchant configures it themselves by saving a
        # wallet number, which is what sets is_configured.
        if not payment_settings.get("vodafone_cash", {}).get("is_configured"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Vodafone Cash is not configured. Save your wallet number first."
                ),
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


# ============ COD Autopilot (004-cod-autopilot) ============


def _build_cod_autopilot_response(store: Store) -> CodAutopilotResponse:
    from src.application.services.cod_autopilot_service import (
        get_cod_autopilot_settings,
    )

    config = get_cod_autopilot_settings(store.settings)
    cod_trust = _get_cod_trust_settings(store.settings)
    return CodAutopilotResponse(
        enabled=config.enabled,
        digest_hour=config.digest_hour,
        delivery_check_delay_days=config.delivery_check_delay_days,
        delivery_check_retry_days=config.delivery_check_retry_days,
        delivery_check_max_attempts=config.delivery_check_max_attempts,
        assumed_delivered_days=config.assumed_delivered_days,
        # The daily digest goes to the store's contact phone (research
        # R-10); without one the digest flow silently never fires, so the
        # UI must surface it.
        digest_deliverable=bool(store.contact_phone),
        auto_rto_days=int(cod_trust.get("auto_rto_days", 14)),
    )


@router.get(
    "/cod-autopilot",
    response_model=SuccessResponse[CodAutopilotResponse],
    summary="Get COD Autopilot settings",
    operation_id="get_cod_autopilot_settings",
)
async def get_cod_autopilot_settings_endpoint(
    store: Annotated[Store, Depends(get_current_store)],
):
    """COD Autopilot (WhatsApp ship digest + delivery checks + assumed-
    delivered fallback) settings for the store. Defaults apply when the
    section is absent."""
    return SuccessResponse(
        data=_build_cod_autopilot_response(store),
        message="COD Autopilot settings retrieved",
    )


@router.patch(
    "/cod-autopilot",
    response_model=SuccessResponse[CodAutopilotResponse],
    summary="Update COD Autopilot settings",
    operation_id="update_cod_autopilot_settings",
)
async def update_cod_autopilot_settings_endpoint(
    request: UpdateCodAutopilotRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Update COD Autopilot settings. Disabling takes effect immediately
    (FR-023) — the beat sweeps re-read settings every run and skip
    disabled stores; no order state is altered."""
    from src.application.services.cod_autopilot_service import COD_AUTOPILOT_DEFAULTS

    settings = dict(store.settings) if store.settings else {}
    section = dict(COD_AUTOPILOT_DEFAULTS)
    raw = settings.get("cod_autopilot") or {}
    if isinstance(raw, dict):
        section.update({k: v for k, v in raw.items() if k in COD_AUTOPILOT_DEFAULTS})

    for key in COD_AUTOPILOT_DEFAULTS:
        value = getattr(request, key, None)
        if value is not None:
            section[key] = value

    settings["cod_autopilot"] = section
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data=_build_cod_autopilot_response(store),
        message="COD Autopilot settings updated",
    )


# ============ Storefront password protection ============
#
# A pre-launch "password page" gate, Shopify-style. Stored under
# ``store.settings.password_protected = {enabled, password_hash}`` — the
# same shape the Next.js storefront reads (src/lib/store-lock.ts) and the
# /api/storefront/unlock route compares against. We hash server-side with
# SHA-256 hex so the plaintext is never persisted, matching the
# storefront's ``hashPassword`` (sha256 of the visitor's input).


class StorefrontPasswordResponse(BaseModel):
    enabled: bool
    has_password: bool
    # Platform billing lock — set when the tenant is read_only, cleared by a
    # wallet top-up (PAYG) or an activated subscription. The merchant cannot
    # turn it off from here, so the hub shows it as state, not a toggle. The
    # password is returned so they can still open their own storefront.
    billing_locked: bool = False
    billing_lock_reason: str | None = None
    billing_lock_password: str | None = None


class UpdateStorefrontPasswordRequest(BaseModel):
    enabled: bool
    # Plaintext; only sent when setting/changing the password. Omit to keep
    # the existing password while toggling ``enabled``.
    password: str | None = Field(default=None, max_length=200)


def _password_protected(store_settings: dict | None) -> dict:
    raw = (store_settings or {}).get("password_protected") or {}
    return raw if isinstance(raw, dict) else {}


@router.get(
    "/storefront-password",
    response_model=SuccessResponse[StorefrontPasswordResponse],
    summary="Get storefront password-protection status",
    operation_id="get_storefront_password",
)
async def get_storefront_password(
    store: Annotated[Store, Depends(get_current_store)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Return whether the storefront is password-protected (never the hash).

    Also reports the platform billing lock, which gates the storefront while
    the tenant sits in ``read_only``. That one is state rather than a switch:
    only a top-up or a paid subscription clears it.
    """
    from sqlalchemy import select

    from src.application.services.storefront_lock import (
        lock_password,
        resolve_lock_reason,
    )
    from src.infrastructure.database.models.public.tenant import TenantModel

    pp = _password_protected(store.settings)
    tenant = (
        await session.execute(
            select(TenantModel).where(TenantModel.id == store.tenant_id)
        )
    ).scalar_one_or_none()
    reason = await resolve_lock_reason(session, tenant)

    return SuccessResponse(
        data=StorefrontPasswordResponse(
            enabled=bool(pp.get("enabled")),
            has_password=bool(pp.get("password_hash")),
            billing_locked=reason is not None,
            billing_lock_reason=reason,
            billing_lock_password=lock_password(store.id) if reason else None,
        ),
        message="Storefront password status retrieved",
    )


@router.put(
    "/storefront-password",
    response_model=SuccessResponse[StorefrontPasswordResponse],
    summary="Enable/disable storefront password & set the password",
    operation_id="update_storefront_password",
)
async def update_storefront_password(
    request: UpdateStorefrontPasswordRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Set the storefront's pre-launch password gate.

    Hashes the password (SHA-256) server-side and stores it under
    ``settings.password_protected``. Disabling keeps the hash so the merchant
    can re-enable without retyping. Refuses to enable without a password.
    """
    settings = dict(store.settings) if store.settings else {}
    pp = dict(_password_protected(settings))

    if request.password is not None and request.password.strip():
        pp["password_hash"] = hashlib.sha256(
            request.password.encode("utf-8")
        ).hexdigest()

    if request.enabled and not pp.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Set a password before enabling protection.",
        )

    pp["enabled"] = bool(request.enabled)
    settings["password_protected"] = pp
    store.settings = settings
    await store_repo.update(store)

    # Storefront reads the gate from the cached store payload — bust it so
    # the change takes effect on the next request.
    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )

    return SuccessResponse(
        data=StorefrontPasswordResponse(
            enabled=pp["enabled"],
            has_password=bool(pp.get("password_hash")),
        ),
        message="Storefront password updated",
    )


# ============ Product Labels ============


def _stored_product_labels(store_settings: dict | None) -> list[ProductLabelDef]:
    """Parse ``settings.product_labels`` defensively — malformed rows are
    skipped rather than failing the whole response."""
    raw = (store_settings or {}).get("product_labels")
    if not isinstance(raw, list):
        return []
    labels: list[ProductLabelDef] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            labels.append(ProductLabelDef.model_validate(item))
        except Exception:  # noqa: BLE001 — tolerate legacy/hand-edited rows
            continue
    return labels


@router.get(
    "/product-labels",
    response_model=SuccessResponse[ProductLabelsResponse],
    summary="Get the store's custom product-label definitions",
    operation_id="get_product_labels",
)
async def get_product_labels(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Custom labels only — built-in presets (new/sale/bestseller/limited)
    live in the hub, not in settings."""
    return SuccessResponse(
        data=ProductLabelsResponse(labels=_stored_product_labels(store.settings)),
        message="Product labels retrieved",
    )


@router.put(
    "/product-labels",
    response_model=SuccessResponse[ProductLabelsResponse],
    summary="Replace the store's custom product-label definitions",
    operation_id="update_product_labels",
)
async def update_product_labels(
    request: UpdateProductLabelsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Full-replace of ``settings.product_labels`` (the hub always sends the
    complete list). Read-merge-write on the settings dict — only this key is
    touched, everything else (payment config!) is preserved. Duplicate keys
    are collapsed keeping the last occurrence.

    Definition changes fan out to labeled products so the denormalized
    ``attributes.label`` text never goes stale:
      - renamed definition → text rewritten on every product carrying the key
      - deleted definition → label stripped (products fall back to no label)
    Built-in presets never live in settings, so they can't be renamed or
    deleted here.
    """
    old = {label.key: label for label in _stored_product_labels(store.settings)}

    deduped: dict[str, ProductLabelDef] = {}
    for label in request.labels:
        deduped[label.key] = label

    settings = dict(store.settings) if store.settings else {}
    settings["product_labels"] = [label.model_dump() for label in deduped.values()]
    store.settings = settings
    await store_repo.update(store)

    for key, label in deduped.items():
        previous = old.get(key)
        if previous and (
            previous.text_en != label.text_en or previous.text_ar != label.text_ar
        ):
            await product_repo.propagate_label_text(
                store.id, key, label.text_en, label.text_ar
            )
    for key in old:
        if key not in deduped:
            await product_repo.clear_label(store.id, key)

    return SuccessResponse(
        data=ProductLabelsResponse(labels=list(deduped.values())),
        message="Product labels updated",
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
        "apple_pay_integration_id": request.apple_pay_integration_id,
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
        # Plain (non-secret) flag so the storefront payment-method endpoints
        # can surface Apple Pay cheaply without decrypting the blob. The
        # Apple Pay integration ID itself lives inside encrypted_credentials.
        "apple_pay_enabled": bool(request.apple_pay_integration_id),
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )

    logger.info(f"Paymob credentials saved for store {store.id}")

    # Live validation probe — create a tiny Paymob intention with the store's
    # currency + the just-entered integration IDs so the merchant sees the
    # actual reason (e.g. "incorrect combination of Integration ID + Currency")
    # AT SETUP TIME instead of only when a shopper fails at checkout. Non-fatal:
    # the credentials are already saved; we only surface a warning.
    validation_warning: str | None = None
    try:
        from src.infrastructure.external_services.paymob import (
            PaymobPaymentService,
        )

        probe = PaymobPaymentService(
            secret_key=request.secret_key,
            public_key=request.public_key,
            hmac_secret=request.hmac_secret,
            card_integration_id=request.card_integration_id,
            wallet_integration_id=request.wallet_integration_id,
            apple_pay_integration_id=request.apple_pay_integration_id,
        )
        store_ccy = (
            store.default_currency.value
            if hasattr(store.default_currency, "value")
            else str(store.default_currency or "EGP")
        )
        await probe.create_payment_intent(
            amount=100,
            currency=store_ccy,
            metadata={
                "order_id": f"setup-check-{store.id}",
                "billing_data": {},
            },
        )
    except Exception as exc:  # noqa: BLE001 — surface any probe failure
        validation_warning = str(exc)
        logger.info(
            "Paymob credential validation probe failed for store %s: %s",
            store.id,
            validation_warning,
        )

    return SuccessResponse(
        data=PaymobCredentialsResponse(
            is_configured=True,
            public_key_masked=secrets.mask_credential(request.public_key),
            secret_key_masked=secrets.mask_credential(request.secret_key),
            hmac_secret_masked=secrets.mask_credential(request.hmac_secret),
            card_integration_id=request.card_integration_id,
            wallet_integration_id=request.wallet_integration_id,
            apple_pay_integration_id=request.apple_pay_integration_id,
            last_configured=payment_settings["paymob"]["last_configured"],
            validation_warning=validation_warning,
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
            apple_pay_integration_id=creds.get("apple_pay_integration_id"),
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
        # Plain opt-in flag: surfaced at checkout + toggles Apple Pay in the
        # Kashier session. No secret, so it lives in the plain settings dict.
        "apple_pay_enabled": bool(request.apple_pay_enabled),
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
            apple_pay_enabled=request.apple_pay_enabled,
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
            apple_pay_enabled=kashier_settings.get("apple_pay_enabled", False),
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


# ============ Moyasar Credentials (KSA) ============


@router.put(
    "/payment/moyasar/credentials",
    response_model=SuccessResponse[MoyasarCredentialsResponse],
    summary="Save Moyasar credentials",
    operation_id="save_moyasar_credentials",
)
async def save_moyasar_credentials(
    request: SaveMoyasarCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save or update Moyasar payment gateway credentials for the store."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()

    credential_data = {
        "secret_key": request.secret_key,
        "publishable_key": request.publishable_key,
        "webhook_secret": request.webhook_secret,
    }

    encrypted = await secrets.encrypt(credential_data, key_id)
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["moyasar"] = {
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

    logger.info(f"Moyasar credentials saved for store {store.id}")

    return SuccessResponse(
        data=MoyasarCredentialsResponse(
            is_configured=True,
            secret_key_masked=secrets.mask_credential(request.secret_key),
            publishable_key_masked=(
                secrets.mask_credential(request.publishable_key)
                if request.publishable_key
                else None
            ),
            webhook_secret_masked=(
                secrets.mask_credential(request.webhook_secret)
                if request.webhook_secret
                else None
            ),
            last_configured=payment_settings["moyasar"]["last_configured"],
        ),
        message="Moyasar credentials saved successfully",
    )


@router.get(
    "/payment/moyasar/credentials",
    response_model=SuccessResponse[MoyasarCredentialsResponse],
    summary="Get Moyasar credentials status",
    operation_id="get_moyasar_credentials",
)
async def get_moyasar_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Moyasar credential status for the store."""
    settings = store.settings or {}
    moyasar_settings = settings.get("payment", {}).get("moyasar", {})

    if not moyasar_settings.get("encrypted_credentials"):
        return SuccessResponse(
            data=MoyasarCredentialsResponse(is_configured=False),
            message="Moyasar credentials not configured",
        )

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = moyasar_settings["encryption_key_id"]
    encrypted = base64.b64decode(moyasar_settings["encrypted_credentials"])

    try:
        creds = await secrets.decrypt(encrypted, key_id)
    except Exception:
        logger.error(f"Failed to decrypt Moyasar credentials for store {store.id}")
        return SuccessResponse(
            data=MoyasarCredentialsResponse(
                is_configured=True,
                last_configured=moyasar_settings.get("last_configured"),
            ),
            message="Credentials configured but could not be read. Please re-save.",
        )

    return SuccessResponse(
        data=MoyasarCredentialsResponse(
            is_configured=True,
            secret_key_masked=secrets.mask_credential(creds.get("secret_key", "")),
            publishable_key_masked=(
                secrets.mask_credential(creds["publishable_key"])
                if creds.get("publishable_key")
                else None
            ),
            webhook_secret_masked=(
                secrets.mask_credential(creds["webhook_secret"])
                if creds.get("webhook_secret")
                else None
            ),
            last_configured=moyasar_settings.get("last_configured"),
        ),
        message="Moyasar credentials retrieved successfully",
    )


@router.delete(
    "/payment/moyasar/credentials",
    response_model=SuccessResponse[MoyasarCredentialsResponse],
    summary="Remove Moyasar credentials",
    operation_id="delete_moyasar_credentials",
)
async def delete_moyasar_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove Moyasar credentials and disable Moyasar payments."""
    settings = store.settings or {}
    payment_settings = settings.get("payment", _get_default_payment_settings())

    payment_settings["moyasar"] = {
        "enabled": False,
        "is_configured": False,
        "last_configured": None,
    }

    settings["payment"] = payment_settings
    store.settings = settings
    await store_repo.update(store)

    logger.info(f"Moyasar credentials removed for store {store.id}")

    return SuccessResponse(
        data=MoyasarCredentialsResponse(is_configured=False),
        message="Moyasar credentials removed successfully",
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

    # Collect requested toggles from both shapes: the new carrier-keyed
    # `carriers` map, and the legacy per-carrier fields the hub still
    # sends. Legacy first so an explicit `carriers` entry wins.
    requested: dict[str, bool] = {}
    for slug in shipping_carrier_keys():
        legacy = getattr(request, f"{slug}_enabled", None)
        if legacy is not None:
            requested[slug] = bool(legacy)
    for slug, value in (request.carriers or {}).items():
        if slug not in shipping_carrier_keys():
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "UNKNOWN_CARRIER",
                    "message_en": f"Unknown carrier '{slug}'.",
                    "message_ar": f"شركة شحن غير معروفة '{slug}'.",
                    "supported_carriers": shipping_carrier_keys(),
                },
            )
        requested[slug] = bool(value)

    from src.application.services.carrier_resolver import carrier_name

    for slug, enabled in requested.items():
        entry = shipping_settings.setdefault(
            slug, {"enabled": False, "is_configured": False, "last_configured": None}
        )
        # `manual` needs no credentials, so it has no configured gate —
        # preserved from the original behaviour.
        if enabled and slug != "manual" and not entry.get("is_configured"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "CARRIER_NOT_CONFIGURED",
                    "message_en": (
                        f"{carrier_name(slug, 'en')} is not configured. "
                        f"Add its credentials first."
                    ),
                    "message_ar": (
                        f"{carrier_name(slug, 'ar')} مش متظبط. ضيف بيانات الربط الأول."
                    ),
                    "carrier": slug,
                },
            )
        entry["enabled"] = enabled
    if request.free_shipping_threshold is not None:
        shipping_settings["free_shipping_threshold"] = request.free_shipping_threshold
    if request.restrict_to_zones is not None:
        shipping_settings["restrict_to_zones"] = request.restrict_to_zones

    # Save settings
    settings["shipping"] = shipping_settings
    store.settings = settings
    await store_repo.update(store)

    # Auto-complete add_shipping onboarding step when any carrier is enabled
    any_enabled = any(
        shipping_settings.get(c, {}).get("enabled", False)
        for c in shipping_carrier_keys()
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

    **Superseded by** ``PUT /shipments/carriers/{slug}/credentials``, which
    works for every registered carrier. Kept because existing clients call
    this path, but it now runs the same shared logic so both routes behave
    identically.

    Two behaviours changed here, deliberately:

    * It used to set ``is_configured: True`` **without ever calling
      Bosta**, so a typo'd API key showed a green "Live" badge. It now
      verifies and persists the result.
    * It used to set ``enabled: True``, silently switching the carrier on
      as a side effect of saving a key. Enabling is an explicit action in
      shipping settings; a carrier whose credentials Bosta rejects must
      not be enabled at all.
    """
    from src.api.v1.routes.stores.carriers import _run_verification
    from src.application.services.carrier_credentials import store_credentials

    settings = await store_credentials(
        store.settings,
        "bosta",
        {
            "api_key": request.api_key,
            "business_id": request.business_id,
            "webhook_secret": request.webhook_secret,
        },
    )
    shipping_settings = settings["shipping"]
    entry = shipping_settings["bosta"]
    entry["last_configured"] = datetime.now(UTC).isoformat()
    entry["auto_create_shipment"] = request.auto_create_shipment

    verified, verification_error = await _run_verification("bosta", settings)
    entry["verified"] = verified
    entry["verified_at"] = datetime.now(UTC).isoformat() if verified else None
    entry["verification_error"] = verification_error
    # Never enable as a side effect of saving, and never disable as a side
    # effect of a failed check — verification fails for carrier outages too,
    # and switching a live store's shipping off over a timeout is worse than
    # the false-green badge this replaced. Enabling stays the merchant's
    # explicit action in shipping settings.
    entry.setdefault("enabled", False)
    store.settings = settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.ADD_SHIPPING
    )

    logger.info(f"Bosta credentials saved for store {store.id} (verified={verified})")

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    return SuccessResponse(
        data=BostaCredentialsResponse(
            is_configured=True,
            api_key_masked=get_secrets_manager().mask_credential(request.api_key),
            business_id=request.business_id,
            auto_create_shipment=request.auto_create_shipment,
            last_configured=entry["last_configured"],
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
    """Remove Bosta credentials and disable Bosta shipping.

    Superseded by ``DELETE /shipments/carriers/{slug}/credentials``; shares
    its implementation so the two paths cannot drift.
    """
    from src.application.services.carrier_credentials import clear_credentials

    settings = clear_credentials(store.settings, "bosta")
    entry = settings["shipping"]["bosta"]
    entry["last_configured"] = None
    for key in ("verified", "verified_at", "verification_error"):
        entry.pop(key, None)
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

    # Commit the publish write BEFORE busting any cache, so both the Redis
    # invalidation and the Next.js revalidation run against committed data.
    # Otherwise the request session commits only in get_db_session's finalizer
    # (after this handler returns), letting a racing storefront read re-cache
    # the stale, pre-commit row. See commit_and_restore_rls.
    from src.infrastructure.database.connection import commit_and_restore_rls

    await commit_and_restore_rls(store_repo.session)

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

            await revalidate_on_customization_publish(
                store.subdomain,
                str(store.id),
                custom_domain=store.custom_domain,
            )
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


async def _warm_public_asset_url(
    url: str, *, attempts: int = 6, budget_seconds: float = 1.5
) -> bool:
    """Block until a freshly-uploaded public asset serves HTTP 200/206.

    Cloudflare R2's public ``*.r2.dev`` edge can return 403 for a short
    window right after an object is written. Polling here means the URL we
    hand back to the editor's image picker is already warm, so it never
    shows a 403 flicker. Uses a 1-byte ranged GET so we don't pull the whole
    object. Best-effort: if it never warms within the budget we return
    ``False`` and let the caller return the URL anyway (the object exists and
    the edge warms within a few seconds). Local storage is served by the
    API's own ``/uploads`` mount and never calls this.
    """
    import asyncio

    import httpx

    delay = budget_seconds / attempts
    try:
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
            for attempt in range(attempts):
                try:
                    resp = await client.get(url, headers={"Range": "bytes=0-0"})
                    if resp.status_code in (200, 206):
                        return True
                except httpx.HTTPError:
                    pass
                if attempt < attempts - 1:
                    await asyncio.sleep(delay)
    except Exception:
        # Never let warm-up failure break an otherwise-successful upload.
        pass
    return False


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

    # Build the object key under the store's customization prefix. This MUST
    # be passed as ``key=`` (not ``filename=``) — otherwise the storage
    # service generates its own ``stores/<uuid>`` key and discards this
    # prefix, so the list endpoint (which queries ``customization/{id}/``)
    # would never find the upload → the Library tab shows nothing.
    ext = (
        file.filename.rsplit(".", 1)[-1]
        if file.filename and "." in file.filename
        else "png"
    )
    object_key = f"customization/{store.id}/{asset_type}_{uuid.uuid4().hex[:8]}.{ext}"

    # Upload to configured storage (Cloudflare R2 / MinIO / local)
    from src.api.dependencies.services import get_storage_service
    from src.config import settings as app_settings
    from src.core.interfaces.services.storage_service import StorageBucket

    storage = get_storage_service()
    result = await storage.upload_file(
        file_content=content,
        filename=file.filename or object_key,
        content_type=file.content_type or "image/png",
        bucket=StorageBucket.STORES,
        key=object_key,
    )
    url = result.url

    # On object storage (R2), wait until the public URL serves before
    # returning it, so the editor's image picker never previews a just-
    # uploaded object during R2's brief edge-propagation 403 window. Local
    # storage is served immediately by the /uploads mount, so it's skipped.
    if app_settings.object_storage_configured:
        await _warm_public_asset_url(url)

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
    """List all uploaded theme/customization assets for this store.

    Each asset is enriched with the merchant-authored ``alt`` text and
    friendly ``name`` from ``store.settings.asset_meta`` (keyed by object
    key) so the Media manager can render and edit them.
    """
    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    prefix = f"customization/{store.id}/"

    # NOTE: assets uploaded before the key-namespace fix landed under the
    # bucket-level ``stores/<uuid>`` prefix (the store id wasn't encoded), so
    # they won't appear here. We deliberately do NOT scan/backfill the legacy
    # ``stores/`` prefix or bulk-move objects — it isn't store-scoped (a
    # cross-tenant read risk) and most stores have no legacy customization
    # assets. New uploads land under this prefix and list correctly.
    try:
        assets = await storage.list_files(prefix)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list assets: {str(e)}")

    asset_meta = (store.settings or {}).get("asset_meta", {}) or {}
    for asset in assets:
        meta = asset_meta.get(asset.get("key"), {}) or {}
        asset["alt"] = meta.get("alt", "")
        asset["name"] = meta.get("name", "")
        transform = meta.get("transform")
        if transform:
            asset["transform"] = transform
    return SuccessResponse(data=assets, message="Assets retrieved successfully")


def _assert_owns_asset_key(store: Store, key: str) -> str:
    """Guard that ``key`` belongs to this store's customization prefix.

    Prevents a merchant from mutating/deleting another tenant's object by
    passing an arbitrary key. Returns the sanitized key.
    """
    from src.core.interfaces.services.storage_service import sanitize_object_key

    safe = sanitize_object_key(key)
    expected_prefix = f"customization/{store.id}/"
    if not safe.startswith(expected_prefix):
        raise HTTPException(
            status_code=403,
            detail="Asset does not belong to this store.",
        )
    return safe


@router.patch(
    "/customization/assets",
    response_model=SuccessResponse[dict],
    summary="Update an asset's alt text / display name",
    operation_id="update_customization_asset_meta",
)
async def update_customization_asset_meta(
    request: UpdateAssetMetaRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Persist library metadata (alt text + friendly name) for one asset.

    Stored in ``store.settings.asset_meta[key]``. The object key / URL is
    never changed — only the metadata. Existing image settings that read
    the URL keep working; the alt here seeds the default alt for the
    image picker.
    """
    safe_key = _assert_owns_asset_key(store, request.key)

    settings = dict(store.settings) if store.settings else {}
    asset_meta = dict(settings.get("asset_meta", {}) or {})
    entry = dict(asset_meta.get(safe_key, {}) or {})

    if request.alt is not None:
        entry["alt"] = request.alt
    if request.name is not None:
        entry["name"] = request.name
    # `transform` is tri-state: absent in the body = leave unchanged; present as
    # an object = set the default focal/zoom; present as null = CLEAR it. We use
    # model_fields_set to tell "absent" from "explicit null" (both deserialize
    # to None on the model). exclude_none keeps the stored blob compact.
    if "transform" in request.model_fields_set:
        if request.transform is not None:
            entry["transform"] = request.transform.model_dump(exclude_none=True)
        else:
            entry.pop("transform", None)

    asset_meta[safe_key] = entry
    settings["asset_meta"] = asset_meta
    store.settings = settings
    await store_repo.update(store)

    return SuccessResponse(
        data={"key": safe_key, **entry},
        message="Asset updated successfully",
    )


@router.delete(
    "/customization/assets",
    response_model=SuccessResponse[dict],
    summary="Delete a customization asset",
    operation_id="delete_customization_asset",
)
async def delete_customization_asset(
    request: DeleteAssetRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Delete an uploaded asset from storage and drop its metadata.

    The merchant is responsible for ensuring the asset isn't still
    referenced by a published section (Shopify behaves the same way — it
    warns but allows). We only verify the key is this store's.
    """
    safe_key = _assert_owns_asset_key(store, request.key)

    from src.api.dependencies.services import get_storage_service

    storage = get_storage_service()
    try:
        await storage.delete_file(safe_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete asset: {str(e)}")

    # Drop any stored metadata for this key so a future re-upload to the
    # same (randomised) key never inherits a stale alt/name.
    settings = dict(store.settings) if store.settings else {}
    asset_meta = dict(settings.get("asset_meta", {}) or {})
    if safe_key in asset_meta:
        asset_meta.pop(safe_key, None)
        settings["asset_meta"] = asset_meta
        store.settings = settings
        await store_repo.update(store)

    return SuccessResponse(
        data={"key": safe_key},
        message="Asset deleted successfully",
    )


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


# ============ Manual rails: InstaPay + Vodafone Cash ============
#
# Both are "push payment" rails with no usable merchant API: publish a
# destination, the customer sends funds out-of-band, then uploads a
# screenshot that OCR rules or the merchant verify. The two rails share
# one implementation in
# ``infrastructure/external_services/manual_transfer/merchant_config.py``;
# only the request/response shapes differ, because the merchant-facing
# nouns do ("IPA" vs "wallet number") and the InstaPay-era response model
# is already consumed by the hub.


def _manual_input(request, *, destination) -> ManualConfigInput:
    """Map either rail's request model onto the shared config input."""
    return ManualConfigInput(
        destination=destination,
        fallback_phone=request.fallback_phone,
        display_name=getattr(request, "ipa_display_name", None)
        or getattr(request, "display_name", None),
        auto_approve_enabled=request.auto_approve_enabled,
        auto_approve_threshold_cents=request.auto_approve_threshold_cents,
        auto_approve_daily_cap_cents=request.auto_approve_daily_cap_cents,
        auto_approve_daily_count=request.auto_approve_daily_count,
        qr_link_url=getattr(request, "qr_link_url", None),
        require_ocr_amount_match=request.require_ocr_amount_match,
        require_ocr_ipa_match=request.require_ocr_ipa_match,
        ocr_amount_tolerance_bps=request.ocr_amount_tolerance_bps,
        require_note_contains_reference=request.require_note_contains_reference,
        require_transaction_ref_match=request.require_transaction_ref_match,
        require_recipient_name_match=request.require_recipient_name_match,
        recipient_name_token=request.recipient_name_token,
    )


def _rail_auto_approve(view: dict, method: ManualPaymentMethod) -> bool:
    """Read the switch, falling back to the RAIL's default — never to True.

    A response that means "I don't know" must not read as "on" for a
    control that decides whether money gets accepted on an unverified
    screenshot.
    """
    return bool(view.get("auto_approve_enabled", default_auto_approve_enabled(method)))


def _instapay_response(view: dict) -> InstapayCredentialsResponse:
    """Render the shared config view in the InstaPay-era response shape."""
    if not view.get("is_configured"):
        return InstapayCredentialsResponse(
            is_configured=False,
            auto_approve_enabled=_rail_auto_approve(view, ManualPaymentMethod.INSTAPAY),
        )
    if view.get("unreadable"):
        return InstapayCredentialsResponse(
            is_configured=True,
            enabled=view.get("enabled", False),
            last_configured=view.get("last_configured"),
        )
    return InstapayCredentialsResponse(
        is_configured=True,
        enabled=view["enabled"],
        ipa_masked=view.get("destination_masked"),
        ipa_display_name=view.get("display_name"),
        fallback_phone=view.get("fallback_phone"),
        auto_approve_enabled=_rail_auto_approve(view, ManualPaymentMethod.INSTAPAY),
        auto_approve_threshold_cents=view.get("auto_approve_threshold_cents"),
        auto_approve_daily_cap_cents=view.get("auto_approve_daily_cap_cents"),
        auto_approve_daily_count=view.get("auto_approve_daily_count"),
        last_configured=view.get("last_configured"),
        qr_image_url=view.get("qr_image_url"),
        qr_link_url=view.get("qr_link_url"),
        ocr_provider=view.get("ocr_provider"),
        require_ocr_amount_match=view["require_ocr_amount_match"],
        require_ocr_ipa_match=view["require_ocr_ipa_match"],
        ocr_amount_tolerance_bps=view["ocr_amount_tolerance_bps"],
        require_note_contains_reference=view["require_note_contains_reference"],
        require_transaction_ref_match=view["require_transaction_ref_match"],
        require_recipient_name_match=view["require_recipient_name_match"],
        recipient_name_token=view.get("recipient_name_token"),
    )


def _vodafone_response(view: dict) -> VodafoneCashCredentialsResponse:
    """Render the shared config view in the Vodafone Cash response shape."""
    if not view.get("is_configured"):
        return VodafoneCashCredentialsResponse(
            is_configured=False,
            auto_approve_enabled=_rail_auto_approve(
                view, ManualPaymentMethod.VODAFONE_CASH
            ),
        )
    if view.get("unreadable"):
        return VodafoneCashCredentialsResponse(
            is_configured=True,
            enabled=view.get("enabled", False),
            last_configured=view.get("last_configured"),
        )
    return VodafoneCashCredentialsResponse(
        is_configured=True,
        enabled=view["enabled"],
        wallet_number_masked=view.get("destination_masked"),
        display_name=view.get("display_name"),
        fallback_phone=view.get("fallback_phone"),
        auto_approve_enabled=_rail_auto_approve(
            view, ManualPaymentMethod.VODAFONE_CASH
        ),
        auto_approve_threshold_cents=view.get("auto_approve_threshold_cents"),
        auto_approve_daily_cap_cents=view.get("auto_approve_daily_cap_cents"),
        auto_approve_daily_count=view.get("auto_approve_daily_count"),
        last_configured=view.get("last_configured"),
        ocr_provider=view.get("ocr_provider"),
        require_ocr_amount_match=view["require_ocr_amount_match"],
        require_ocr_ipa_match=view["require_ocr_ipa_match"],
        ocr_amount_tolerance_bps=view["ocr_amount_tolerance_bps"],
        require_note_contains_reference=view["require_note_contains_reference"],
        require_transaction_ref_match=view["require_transaction_ref_match"],
        require_recipient_name_match=view["require_recipient_name_match"],
        recipient_name_token=view.get("recipient_name_token"),
    )


async def _save_manual_credentials(
    *,
    method: ManualPaymentMethod,
    request,
    destination: str | None,
    store: Store,
    store_repo: StoreRepository,
    onboarding_repo: OnboardingRepository,
) -> dict:
    """Persist one rail's config and return the masked view.

    Shared by both rails so the partial-update carry-forward, the
    destination validation, the encryption and the enabled-state
    preservation can only ever behave one way.
    """
    key = manual_settings_key(method)
    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())

    try:
        block, _destination = await build_config_block(
            method=method,
            existing=payment_settings.get(key) or {},
            data=_manual_input(request, destination=destination),
        )
    except ManualConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    payment_settings[key] = block
    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)

    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )
    logger.info(f"{manual_human_name(method)} credentials saved for store {store.id}")

    return await read_config_view(method=method, block=block)


async def _delete_manual_credentials(
    *,
    method: ManualPaymentMethod,
    store: Store,
    store_repo: StoreRepository,
) -> None:
    """Clear one rail's config and disable it at checkout.

    Existing ``instapay_intents`` rows are not touched — they belong to
    orders already placed, and the merchant still needs to review their
    proofs. New orders can no longer choose the rail.
    """
    store_settings = store.settings or {}
    payment_settings = store_settings.get("payment", _get_default_payment_settings())
    payment_settings[manual_settings_key(method)] = cleared_config_block()
    store_settings["payment"] = payment_settings
    store.settings = store_settings
    await store_repo.update(store)
    logger.info(f"{manual_human_name(method)} credentials removed for store {store.id}")


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

    `request.ipa` and `request.fallback_phone` are both optional — when
    omitted, the existing encrypted blob is decrypted and those values
    carry forward. This lets the merchant edit display name, thresholds,
    or toggle enabled without re-typing the IPA (the UI shows it masked;
    it can never unmask to its true form). First-time saves must include
    `ipa`, and it is now format-checked (`name@bank`) — a typo'd IPA
    silently misroutes a customer's money.
    """
    view = await _save_manual_credentials(
        method=ManualPaymentMethod.INSTAPAY,
        request=request,
        destination=request.ipa,
        store=store,
        store_repo=store_repo,
        onboarding_repo=onboarding_repo,
    )
    return SuccessResponse(
        data=_instapay_response(view),
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
    block = (store.settings or {}).get("payment", {}).get("instapay", {})
    view = await read_config_view(method=ManualPaymentMethod.INSTAPAY, block=block)
    if not view.get("is_configured"):
        return SuccessResponse(
            data=_instapay_response(view),
            message="InstaPay credentials not configured",
        )
    if view.get("unreadable"):
        logger.error(f"Failed to decrypt InstaPay credentials for store {store.id}")
        return SuccessResponse(
            data=_instapay_response(view),
            message="InstaPay credentials configured but unreadable",
        )
    return SuccessResponse(data=_instapay_response(view))


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
    """Remove the stored InstaPay IPA and disable InstaPay at checkout."""
    await _delete_manual_credentials(
        method=ManualPaymentMethod.INSTAPAY,
        store=store,
        store_repo=store_repo,
    )
    return SuccessResponse(
        data=InstapayCredentialsResponse(is_configured=False),
        message="InstaPay credentials removed successfully",
    )


# ============ Vodafone Cash Credentials ============
#
# Deliberately NOT wired to the gateway-validator path. A
# ``VodafoneCashValidator`` existed that demanded merchant_id/api_key/pin
# — Vodafone's merchant API, which requires a commercial partnership and
# an aggregator. It could never return is_configured=True, which made
# the enable toggle above unreachable and made the feature look
# half-built. The rail NUMU actually runs is manual, and a wallet number
# is the only thing to validate.


@router.put(
    "/payment/vodafone-cash/credentials",
    response_model=SuccessResponse[VodafoneCashCredentialsResponse],
    summary="Save Vodafone Cash credentials",
    operation_id="save_vodafone_cash_credentials",
)
async def save_vodafone_cash_credentials(
    request: SaveVodafoneCashCredentialsRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Save Vodafone Cash configuration for the store.

    The wallet number is normalized to ``010XXXXXXXX`` (accepting
    ``+20``/``0020`` prefixes, separators, and Arabic-Indic digits — all
    of which merchants paste out of the Ana Vodafone app) and rejected
    if it isn't a Vodafone Egypt mobile number. Storing it normalized
    matters downstream: the OCR match rule compares against this exact
    string, and the checkout panel offers it as a tap-to-copy value that
    has to be dialable as-is.

    `wallet_number` is optional on updates and carries forward from the
    encrypted blob; first-time saves must include it.
    """
    view = await _save_manual_credentials(
        method=ManualPaymentMethod.VODAFONE_CASH,
        request=request,
        destination=request.wallet_number,
        store=store,
        store_repo=store_repo,
        onboarding_repo=onboarding_repo,
    )
    return SuccessResponse(
        data=_vodafone_response(view),
        message="Vodafone Cash settings saved successfully",
    )


@router.get(
    "/payment/vodafone-cash/credentials",
    response_model=SuccessResponse[VodafoneCashCredentialsResponse],
    summary="Get Vodafone Cash credentials status",
    operation_id="get_vodafone_cash_credentials",
)
async def get_vodafone_cash_credentials(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get masked Vodafone Cash config status for the store."""
    block = (store.settings or {}).get("payment", {}).get("vodafone_cash", {})
    view = await read_config_view(method=ManualPaymentMethod.VODAFONE_CASH, block=block)
    if not view.get("is_configured"):
        return SuccessResponse(
            data=_vodafone_response(view),
            message="Vodafone Cash settings not configured",
        )
    if view.get("unreadable"):
        logger.error(f"Failed to decrypt Vodafone Cash settings for store {store.id}")
        return SuccessResponse(
            data=_vodafone_response(view),
            message="Vodafone Cash settings configured but unreadable",
        )
    return SuccessResponse(data=_vodafone_response(view))


@router.delete(
    "/payment/vodafone-cash/credentials",
    response_model=SuccessResponse[VodafoneCashCredentialsResponse],
    summary="Remove Vodafone Cash credentials",
    operation_id="delete_vodafone_cash_credentials",
)
async def delete_vodafone_cash_credentials(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Remove the stored wallet number and disable Vodafone Cash."""
    await _delete_manual_credentials(
        method=ManualPaymentMethod.VODAFONE_CASH,
        store=store,
        store_repo=store_repo,
    )
    return SuccessResponse(
        data=VodafoneCashCredentialsResponse(is_configured=False),
        message="Vodafone Cash settings removed successfully",
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

# Window the delivery counters cover. Matches the recent-failure window the
# admin fleet view already uses, so "failing in the last day" and "stuck in
# the last day" are the same day.
_DELIVERY_WINDOW_HOURS = 24

from sqlalchemy import select as _select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified as _flag_modified  # noqa: E402

from src.api.dependencies.database import get_db as _get_db  # noqa: E402
from src.api.v1.schemas.tenant.channels import (  # noqa: E402
    ConnectTikTokShopRequest,
    TikTokShopStatusResponse,
)
from src.api.v1.schemas.tenant.tracking import (  # noqa: E402
    MetaDeliveryHealth,
    MetaEventLogEntry,
    MetaMatchKeyCoverage,
    MetaMatchQualityEvent,
    MetaMatchQualityResponse,
    MetaTrackingResponse,
    MetaTrackingStatusResponse,
    SaveMetaTrackingRequest,
    SaveTikTokTrackingRequest,
    SendMetaTestEventRequest,
    SendMetaTestEventResponse,
    SendTikTokTestEventRequest,
    SendTikTokTestEventResponse,
    TikTokEventLogEntry,
    TikTokReportResponse,
    TikTokTrackingResponse,
    TikTokTrackingStatusResponse,
    TrackingSettingsResponse,
    VerifyConnectionResponse,
)
from src.application.services.meta_tracking_resolver import (  # noqa: E402
    resolve_mode,
)
from src.application.services.tiktok_tracking_resolver import (  # noqa: E402
    resolve_tiktok_mode,
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
        if mode == "pixel_only":
            # Derived from the SERVER event log, which a pixel-only store
            # never writes to — so "configured_no_events" was permanently
            # wrong and read as broken for a working setup.
            status_label = "browser_only"
        elif not recent:
            status_label = "configured_no_events"
        else:
            # Report "failing" on the FAILURE RATE, with no minimum-volume
            # floor. The old rule needed 5 recent events before it would ever
            # say "failing", so a low-volume store whose every event 4xx'd
            # rendered a green "connected" badge indefinitely — which is
            # exactly the store most likely to have a broken setup and least
            # likely to notice.
            #
            # In-flight rows (response_status IS NULL) are NOT counted as
            # failures: they are pending, and treating them as errors would
            # flash red on every burst of traffic.
            settled = [r for r in recent if r.response_status is not None]
            failed = sum(1 for r in settled if r.response_status >= 400)
            if not settled:
                status_label = "pending"
            elif failed / len(settled) > 0.5:
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
        ad_account_id=cfg.get("ad_account_id"),
        page_id=cfg.get("page_id"),
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
    """Return the per-channel tracking config — Meta + TikTok."""
    meta = await _build_meta_response(db, store)
    tiktok = await _build_tiktok_response(db, store)
    return SuccessResponse(
        data=TrackingSettingsResponse(meta=meta, tiktok=tiktok),
        message="Tracking settings retrieved",
    )


@router.get(
    "/tracking/validation-contract",
    response_model=SuccessResponse[dict],
    summary="Get the tracking-credential validation contract",
    operation_id="get_tracking_validation_contract",
)
async def get_tracking_validation_contract(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Serve the pixel-ID / token / test-code rules the API enforces.

    The merchant hub drives its client-side validation from this instead of
    retyping the regexes. That is the whole point: the same rules used to
    live as literals in three repos and they drifted — a real 17-digit Meta
    Pixel ID was rejected by a ``^\\d{15,16}$`` whitelist in the hub AND
    here, while the storefront accepted it. With the API as the authority a
    rule can be loosened without a frontend deploy, and the hub can never be
    stricter than the endpoint it posts to.

    Store-scoped only for auth symmetry with the rest of the tracking panel;
    the payload is platform-wide and contains no store data. Patterns use
    syntax that means the same thing in Python ``re`` and ECMAScript, so the
    hub can hand them to ``new RegExp`` unchanged.
    """
    from src.api.v1.schemas.tenant.tracking_validation import validation_contract

    return SuccessResponse(
        data=validation_contract(),
        message="Tracking validation contract retrieved",
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
            # ⚠️ Meta CAPI credentials are keyed on TENANT, not store —
            # `idx_service_credentials_tenant_service` is UNIQUE on
            # (tenant_id, service_type, service_name). A tenant with two
            # stores under different Meta Business Managers therefore has
            # exactly ONE token slot, and saving on store B silently replaces
            # store A's token; A's events then 4xx against a pixel that token
            # cannot write to, with nothing surfaced to either merchant.
            #
            # Fixing that properly means adding `store_id` to the shared
            # credential table and its unique index, then backfilling — a
            # change that touches every integration (WhatsApp, payments,
            # shipping), so it is deliberately NOT bundled into a tracking
            # fix. What is safe here is making the collision VISIBLE rather
            # than silent: stamp the owning store, and log when it changes.
            _prior_store = (existing_cred.extra_metadata or {}).get("store_id")
            if _prior_store and str(_prior_store) != str(store.id):
                logger.warning(
                    "meta_capi_credential_reassigned tenant=%s from_store=%s "
                    "to_store=%s - this tenant has ONE Meta token slot; the "
                    "previous store's events will now fail",
                    store.tenant_id,
                    _prior_store,
                    store.id,
                )
            existing_cred.extra_metadata = {
                "pixel_id": request.pixel_id,
                "store_id": str(store.id),
            }
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
    # Precedence: what the merchant just pasted from Business Manager wins,
    # then whatever is already stored (so a save that omits the field is a
    # no-op for it), and only a store that has never had one falls back to a
    # generated placeholder. The generated value cannot verify anything —
    # Meta looks for the token IT issued — it only keeps the storefront's
    # <meta> tag non-empty for stores predating this field.
    domain_token = (
        request.domain_verification_token
        or meta_cfg.get("domain_verification_token")
        or _stdlib_secrets.token_urlsafe(24)
    )

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
        # ── No-clobber contract ───────────────────────────────────────
        # Everything below follows the same rule the Meta Business IDs
        # already documented: a field the request did not supply keeps the
        # value already on record. It previously did NOT, so any client that
        # posted a partial panel — an older hub build, the mobile app, a
        # merchant saving from a screen that doesn't render these controls —
        # silently erased the store's multi-pixel list, its COD Purchase
        # trigger, its Lead trigger, its WhatsApp-lead opt-in and its granular
        # consent policy. Nothing surfaced the loss; the save returned 200 and
        # the next event simply behaved differently.
        #
        # Wave 2 Phase 12 — COD-aware Purchase / Lead firing config.
        # None preserves legacy behavior (paymob/fawry webhooks remain
        # the sole Purchase source).
        "purchase_trigger": (
            request.purchase_trigger
            if request.purchase_trigger is not None
            else meta_cfg.get("purchase_trigger")
        ),
        "lead_trigger": (
            request.lead_trigger
            if request.lead_trigger is not None
            else meta_cfg.get("lead_trigger")
        ),
        # Wave 2 Phase 15 — fire Lead when COD customer confirms via
        # WhatsApp reply. Off by default — opt-in. Tri-state on the wire
        # (True / False / omitted) so "off" stays distinguishable from
        # "not sent"; a plain bool default could only ever mean "off".
        "whatsapp_lead_enabled": (
            bool(request.whatsapp_lead_enabled)
            if request.whatsapp_lead_enabled is not None
            else bool(meta_cfg.get("whatsapp_lead_enabled"))
        ),
        # Wave 2 Phase 13 — store-level multi-pixel list. None when
        # the merchant is still on the legacy single-pixel path.
        "pixels": new_pixels if new_pixels is not None else meta_cfg.get("pixels"),
        # Wave 3 Phase 18 — granular consent policy. None preserves
        # the legacy single-toggle behavior gated on consent_required.
        "consent_settings": (
            request.consent_settings.model_dump()
            if request.consent_settings is not None
            else meta_cfg.get("consent_settings")
        ),
        # Meta Business connection IDs — only overwrite when the request
        # supplies a value. Sending the panel without re-entering them
        # (legacy panel state) must NOT wipe the existing IDs, otherwise
        # the audience-sync and Promote-on-Meta gates would silently
        # break on every settings save.
        "ad_account_id": (
            request.ad_account_id
            if request.ad_account_id is not None
            else meta_cfg.get("ad_account_id")
        ),
        "page_id": (
            request.page_id if request.page_id is not None else meta_cfg.get("page_id")
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
    # Disable every entry in the multi-pixel array too, and clear the legacy
    # flat id. Only the top-level flags were being cleared — but the
    # storefront resolves `pixels[]` FIRST and ignores those flags, so a
    # multi-pixel store that hit "Disconnect" kept firing the browser Pixel
    # on every page. A disconnect that does not disconnect is worse than no
    # button: the merchant believes they have stopped sending data to Meta.
    if isinstance(meta_cfg.get("pixels"), list):
        for entry in meta_cfg["pixels"]:
            if isinstance(entry, dict):
                entry["pixel_enabled"] = False
                entry["capi_enabled"] = False
    tracking["meta"] = meta_cfg
    # Legacy flat field, read by older storefront bundles still in ISR cache.
    settings_dict.pop("meta_pixel_id", None)
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
    from src.infrastructure.messaging.tasks.meta_capi import enqueue_capi_event

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

    # `session=None`: a synthetic diagnostic must not be written to the
    # outbox. Persisting it would put a fake Purchase into the retry ladder
    # and into the merchant's delivery counts, where it would read as a real
    # owed conversion.
    await enqueue_capi_event(
        session=None,
        store=store,
        tenant_id=getattr(store, "tenant_id", None),
        store_id=str(store.id),
        pixel_id=pixel_id,
        event_name="Purchase",
        event_id=event_id,
        event_time=int(datetime.now(UTC).timestamp()),
        # The test event is the FIRST thing a merchant validates in Events
        # Manager — arriving without event_source_url on an
        # ``action_source: website`` event shows up there as a "Missing
        # event_source_url" warning and reads as a broken integration. The
        # store's public origin is the honest source for a synthetic event
        # (there is no real page behind it).
        event_source_url=store.store_url,
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
            # IP + UA dominate Event Match Quality for anonymous traffic, so
            # omitting them from this table meant a merchant asking "why is my
            # match quality low?" could not get the answer without a code read
            # — and could not see that the storefront proxy was forwarding the
            # server's own IP instead of the shopper's.
            "had_ip": bool(ud.get("client_ip_address")),
            "had_user_agent": bool(ud.get("client_user_agent")),
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


@router.post(
    "/tracking/meta/verify",
    response_model=SuccessResponse[VerifyConnectionResponse],
    summary="Verify the Meta Pixel against Meta",
    operation_id="verify_meta_tracking_connection",
)
async def verify_meta_tracking_connection(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Ask Meta whether this store's Pixel ID actually exists and is writable.

    ``GET /{version}/{pixel_id}?fields=name,is_active`` with the merchant's own
    CAPI token. A 200 proves three things at once that no local check can: the
    dataset exists, the token has access to it, and the token is still valid.

    Never raises for a "no" answer — a failed verification is a 200 with
    ``verified: false`` and Meta's own message, because the merchant needs to
    read that message. Only a missing store/config is a client error.
    """
    import httpx

    from src.config import settings as _app_settings
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    cfg = _meta_cfg(store)
    pixel_id = (cfg.get("pixel_id") or "").strip()
    if not pixel_id:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="meta",
                error="Save a Pixel ID first.",
            ),
            message="Meta connection not verified",
        )

    cred = await _get_capi_credential(db, store.tenant_id)
    token = ""
    if cred and cred.is_active:
        try:
            sm = get_secrets_manager()
            decrypted = await sm.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            token = decrypted.get("access_token") or ""
        except Exception:
            logger.warning("meta_verify_token_decrypt_failed store_id=%s", store.id)
    if not token:
        # Honest distinction: we could not ASK, which is not the same as Meta
        # saying no. The hub renders these differently.
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="meta",
                error=(
                    "Add a Conversions API access token so we can check this "
                    "Pixel with Meta."
                ),
            ),
            message="Meta connection not verified",
        )

    url = (
        f"https://graph.facebook.com/{_app_settings.meta_graph_api_version}/{pixel_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"fields": "name,is_active", "access_token": token},
            )
        body = resp.json() if resp.content else {}
    except Exception as exc:
        logger.warning("meta_verify_request_failed store_id=%s", store.id)
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="meta",
                error=f"Couldn't reach Meta: {type(exc).__name__}",
            ),
            message="Meta connection not verified",
        )

    if resp.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="meta",
                # Meta's verbatim message — it names the real problem far
                # better than anything we could map it to.
                error=err.get("message") or f"Meta returned HTTP {resp.status_code}",
            ),
            message="Meta connection not verified",
        )

    # Record that the credential actually works.
    #
    # Nothing in the Meta path ever set this: `is_validated` was written False
    # on save and never flipped back, and `last_validated_at` stayed NULL
    # forever — verified in production, where a live store with a working
    # token reported `last_validated_at: null`. The consequence is that token
    # expiry is completely silent: events simply start failing and the panel
    # keeps saying "connected". Stamping it here gives the status badge, the
    # merchant-facing panel and any future re-validation sweep something real
    # to read.
    #
    # Best-effort: a bookkeeping failure must never turn a successful
    # verification into a reported failure.
    try:
        cred = await _get_capi_credential(db, store.tenant_id)
        if cred is not None:
            cred.is_validated = True
            cred.last_validated_at = datetime.now(UTC)
            await db.commit()
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await db.rollback()
        logger.warning(
            "meta_verify_stamp_failed",
            extra={"store_id": str(store.id)},
        )

    return SuccessResponse(
        data=VerifyConnectionResponse(
            verified=True,
            platform="meta",
            name=body.get("name"),
            # `is_active` is absent on some dataset types; absent ≠ inactive,
            # so preserve None rather than coercing to False.
            is_active=body.get("is_active"),
        ),
        message="Meta connection verified",
    )


@router.get(
    "/tracking/meta/match-quality",
    response_model=SuccessResponse[MetaMatchQualityResponse],
    summary="Get Meta Event Match Quality",
    operation_id="get_meta_match_quality",
)
async def get_meta_match_quality(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Latest EMQ snapshot per event, from Meta's Dataset Quality API.

    Reads cached rows written by the ``meta_match_quality_poll`` beat task —
    never calls Meta inline, because the Marketing API rate-limits per app and
    a dashboard must not fail when a third party is slow.

    Empty ``events`` means no poll has landed yet (the store may have just
    connected, or its token may lack the ``ads_read`` scope the Dataset
    Quality API requires — which is a different problem from having no data).
    """
    from src.application.services.meta_match_quality_service import (
        MetaMatchQualityService,
    )

    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixel_id = meta_cfg.get("pixel_id")

    service = MetaMatchQualityService()
    snapshots = await service.get_snapshots(store.id, pixel_id, session=db)

    events = [
        MetaMatchQualityEvent(
            event_name=snap.event_name,
            pixel_id=snap.pixel_id,
            emq_score=snap.emq_score,
            total_events=snap.total_events,
            dedup_rate=snap.dedup_rate,
            event_coverage=snap.event_coverage,
            data_freshness=snap.data_freshness,
            match_keys=[
                MetaMatchKeyCoverage(identifier=key, coverage_percentage=pct)
                for key, pct in sorted(
                    (snap.match_key_coverage or {}).items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )
            ],
            diagnostics=snap.diagnostics or [],
            captured_at=snap.captured_at,
        )
        for snap in snapshots
    ]

    return SuccessResponse(
        data=MetaMatchQualityResponse(
            events=events,
            last_polled_at=max((e.captured_at for e in events), default=None),
            low_score_threshold=MetaMatchQualityService.LOW_EMQ_THRESHOLD,
        ),
        message="Meta match quality retrieved",
    )


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
    # Outbox state over a fixed window, alongside the recent-rows rate. The
    # two answer different questions: the rate says whether sends are working
    # now, the counters say whether anything is stuck. A store can look
    # perfectly healthy on its last 20 rows while a backlog of conversions
    # sits behind it on the retry ladder.
    counts = await repo.delivery_counts(
        since=datetime.now(UTC) - timedelta(hours=_DELIVERY_WINDOW_HOURS),
        store_id=store.id,
    )
    # Only SETTLED rows count toward the failure rate. An in-flight row
    # (response_status IS NULL) is pending, not failed — counting it as a
    # failure made the rate spike on every burst of traffic.
    settled = [r for r in recent if r.response_status is not None]
    failed = sum(1 for r in settled if r.response_status >= 400)
    failure_rate = (failed / len(settled)) if settled else 0.0

    if mode == "off":
        status_label = "disabled"
    elif mode == "pixel_only":
        # The status is derived from the SERVER event log, which a pixel-only
        # store never writes to. Reporting "configured_no_events" for it was
        # permanently wrong — the merchant reads a red state for a working
        # setup. Say what is true: we can see the browser pixel is configured,
        # and we cannot observe its fires from here.
        status_label = "browser_only"
    elif not recent:
        status_label = "configured_no_events"
    elif not settled:
        # Events queued but none acknowledged yet — distinct from both
        # "nothing configured" and "everything is fine".
        status_label = "pending"
    elif failure_rate > 0.5:
        # No minimum-volume floor. The old rule required 5 recent events
        # before it would ever say "failing", so a low-volume store whose
        # every event 4xx'd showed a green "connected" badge indefinitely —
        # precisely the store most likely to be misconfigured and least
        # likely to notice.
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
            delivery=MetaDeliveryHealth(
                pending=counts.get("pending", 0),
                retrying=counts.get("retrying", 0),
                dead_letter=counts.get("dead_letter", 0),
                expired=counts.get("expired", 0),
                failed=counts.get("failed", 0),
                window_hours=_DELIVERY_WINDOW_HOURS,
            ),
        ),
        message="Meta tracking status retrieved",
    )


# ============================================================================
# TikTok Tracking (Pixel + Events API) — sibling of the Meta block above.
# ============================================================================
#
# These endpoints back the merchant-hub "Marketing & Tracking → TikTok" panel.
# Convention: PUT preserves the existing Events API token when the body omits
# ``api_access_token``; **422** when ``api_enabled = true`` and no token is on
# file AND none is provided.
#
# The token is stored as a ``ServiceCredential`` row (TIKTOK_CAPI, encrypted);
# ``store.settings.tracking.tiktok`` carries the public bits (pixel_id, flags,
# debug-mode expiry).
# ============================================================================


def _tiktok_cfg(store: Store) -> dict:
    """Read the ``store.settings.tracking.tiktok`` sub-object (or empty)."""
    return ((store.settings or {}).get("tracking") or {}).get("tiktok") or {}


async def _has_active_tiktok_credential(
    db: _AsyncSession, tenant_id: uuid.UUID
) -> bool:
    """Check whether a TIKTOK_CAPI ServiceCredential row exists + is active."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    q = (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.TIKTOK_CAPI)
        .where(ServiceCredential.is_active.is_(True))
    )
    return (await db.execute(q)).scalar_one_or_none() is not None


async def _get_tiktok_credential(db: _AsyncSession, tenant_id: uuid.UUID):
    """Return the TIKTOK_CAPI ``ServiceCredential`` row (active or not), or None."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    q = (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.TIKTOK_CAPI)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _build_tiktok_response(
    db: _AsyncSession,
    store: Store,
) -> TikTokTrackingResponse:
    """Compose the public TikTok settings shape from store + credential row."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    cfg = _tiktok_cfg(store)
    cred = await _get_tiktok_credential(db, store.tenant_id)
    has_token = cred is not None and cred.is_active
    mode = resolve_tiktok_mode(cfg, has_token)

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
                "tiktok_capi_token_decrypt_failed_for_mask store_id=%s",
                store.id,
            )

    status_label: str = "disabled"
    if mode != "off":
        from src.infrastructure.repositories.tiktok_event_log_repository import (
            TikTokEventLogRepository,
        )

        log_repo = TikTokEventLogRepository(db)
        recent = await log_repo.recent_for_store(store.id, limit=20)
        if not recent:
            status_label = "configured_no_events"
        else:
            last_5_failed = sum(
                1
                for r in recent[:5]
                if r.response_status is None
                or r.response_status >= 400
                or r.response_code not in (0, None)
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

    return TikTokTrackingResponse(
        pixel_id=cfg.get("pixel_id"),
        pixel_enabled=bool(cfg.get("pixel_enabled", False)),
        api_enabled=bool(cfg.get("api_enabled", False)),
        mode=mode,
        api_access_token_masked=masked,
        test_event_code=cfg.get("test_event_code"),
        consent_required=bool(cfg.get("consent_required", False)),
        purchase_trigger=cfg.get("purchase_trigger"),
        pixels=cfg.get("pixels"),
        debug_mode=debug_active,
        debug_mode_expires_at=debug_expires_dt,
        last_validated_at=last_validated_dt,
        status=status_label,
        advertiser_id=cfg.get("advertiser_id"),
    )


@router.put(
    "/tracking/tiktok",
    response_model=SuccessResponse[TikTokTrackingResponse],
    summary="Save TikTok Pixel + Events API settings",
    operation_id="save_tiktok_tracking",
)
async def save_tiktok_tracking(
    request: SaveTikTokTrackingRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Upsert the per-store TikTok tracking config and (optional) API token.

    422 if ``api_enabled = true`` and no token is on file AND none is
    supplied in the body.
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
    tiktok_cfg = tracking.get("tiktok") or {}

    # ── Validation: api_enabled requires a token ───────────────────────
    existing_cred = await _get_tiktok_credential(db, store.tenant_id)
    has_existing_active_token = existing_cred is not None and existing_cred.is_active
    if request.api_enabled and not (
        request.api_access_token or has_existing_active_token
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "api_access_token is required when api_enabled=true "
                "and no token is on file"
            ),
        )

    # ── Persist / update credential when a new token was supplied ─────
    if request.api_access_token:
        sm = get_secrets_manager()
        key_id = await sm.get_current_key_id()
        encrypted = await sm.encrypt(
            {"access_token": request.api_access_token},
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
                service_name=ServiceName.TIKTOK_CAPI,
                credentials_encrypted=encrypted,
                encryption_key_id=key_id,
                is_active=True,
                is_validated=False,
                extra_metadata={"pixel_id": request.pixel_id},
            )
            db.add(new_cred)
        await db.flush()

    # ── Update store.settings.tracking.tiktok in place ────────────────
    debug_expires_iso: str | None = None
    if request.debug_mode:
        debug_expires_iso = (
            datetime.now(UTC) + timedelta(minutes=_DEBUG_MODE_TTL_MINUTES)
        ).isoformat()

    new_pixels = [p.model_dump() for p in request.pixels] if request.pixels else None

    new_tiktok_cfg = {
        **tiktok_cfg,
        "pixel_id": request.pixel_id,
        "pixel_enabled": bool(request.pixel_enabled),
        "api_enabled": bool(request.api_enabled),
        "test_event_code": request.test_event_code,
        "consent_required": bool(request.consent_required),
        "debug_mode_expires_at": debug_expires_iso,
        "purchase_trigger": request.purchase_trigger,
        "pixels": new_pixels,
        # Only overwrite advertiser_id when supplied — don't wipe an
        # existing value on a partial panel save.
        "advertiser_id": (
            request.advertiser_id
            if request.advertiser_id is not None
            else tiktok_cfg.get("advertiser_id")
        ),
    }
    tracking["tiktok"] = new_tiktok_cfg
    settings_dict["tracking"] = tracking
    store.settings = settings_dict
    try:
        _flag_modified(store, "settings")
    except Exception:
        pass
    await store_repo.update(store)

    logger.info(
        "tiktok_tracking_saved store_id=%s pixel_enabled=%s api_enabled=%s "
        "token_updated=%s debug_mode=%s",
        store.id,
        request.pixel_enabled,
        request.api_enabled,
        bool(request.api_access_token),
        request.debug_mode,
    )

    response = await _build_tiktok_response(db, store)
    return SuccessResponse(data=response, message="TikTok tracking saved")


@router.delete(
    "/tracking/tiktok",
    response_model=SuccessResponse[TikTokTrackingResponse],
    summary="Disconnect TikTok tracking",
    operation_id="delete_tiktok_tracking",
)
async def delete_tiktok_tracking(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Disconnect: turn off flags locally + soft-delete credential + audit.

    Unlike Meta there is no server-side OAuth revoke yet (TikTok OAuth is a
    later phase — the token here is a merchant-pasted Events API token).
    The ``tiktok_event_log`` rows are retained as audit data.
    """
    from src.application.services.audit_service import AuditService, EventType

    cred = await _get_tiktok_credential(db, store.tenant_id)
    had_active_token = cred is not None and cred.is_active

    # Turn off flags on store.settings.tracking.tiktok.
    settings_dict: dict = store.settings or {}
    tracking = settings_dict.get("tracking") or {}
    tiktok_cfg = tracking.get("tiktok") or {}
    tiktok_cfg["pixel_enabled"] = False
    tiktok_cfg["api_enabled"] = False
    tiktok_cfg["debug_mode_expires_at"] = None
    tracking["tiktok"] = tiktok_cfg
    settings_dict["tracking"] = tracking
    store.settings = settings_dict
    try:
        _flag_modified(store, "settings")
    except Exception:
        pass
    await store_repo.update(store)

    # Soft-delete the credential row (preserves audit history).
    if cred is not None:
        cred.is_active = False
        await db.flush()

    try:
        await AuditService(db).log(
            event_type=EventType.ADMIN_CONFIG_CHANGE,
            action="tiktok_disconnect",
            resource_type="store_tiktok_integration",
            resource_id=str(store.id),
            store_id=store.id,
            tenant_id=store.tenant_id,
            new_value={
                "pixel_enabled": False,
                "api_enabled": False,
                "had_active_token": had_active_token,
            },
        )
        await db.commit()
    except Exception:
        logger.warning(
            "tiktok_disconnect_audit_log_failed",
            extra={"store_id": str(store.id)},
            exc_info=True,
        )

    logger.info("tiktok_tracking_disconnected store_id=%s", store.id)

    response = await _build_tiktok_response(db, store)
    return SuccessResponse(data=response, message="TikTok tracking disconnected")


@router.post(
    "/tracking/tiktok/test-event",
    response_model=SuccessResponse[SendTikTokTestEventResponse],
    summary="Send a synthetic CompletePayment test event to TikTok",
    operation_id="send_tiktok_test_event",
)
async def send_tiktok_test_event(
    request: SendTikTokTestEventRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Fire a synthetic CompletePayment via the Celery fan-out task.

    Rejects with 422 when the resolved mode is ``off`` or ``pixel_only``
    (no Events API to test).
    """
    from src.infrastructure.messaging.tasks.tiktok_capi import (
        tiktok_capi_send_event,
    )

    cfg = _tiktok_cfg(store)
    has_token = await _has_active_tiktok_credential(db, store.tenant_id)
    mode = resolve_tiktok_mode(cfg, has_token)
    if mode in ("off", "pixel_only"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Test events require the Events API to be enabled. "
                f"Current mode: {mode}"
            ),
        )

    pixel_id = cfg.get("pixel_id")
    if not pixel_id:
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

    # Full plausible identifier set — the Celery worker SHA-256-hashes the
    # PII downstream (tiktok/hashing.py); we pass raw NUMU-internal keys.
    synthetic_user_data = {
        "email": f"numu-test-{store.id}@test.numueg.app",
        "phone": "+201000000000",
        "first_name": "Numu",
        "last_name": "Test",
        "city": "Cairo",
        "country_code": "EG",
        "zip": "11511",
        "customer_id": f"numu-test:{store.id}",
        "ttclid": "NUMU_TEST_TTCLID",
        "ip": "127.0.0.1",
        "user_agent": "NUMU-Test-Event/1.0",
    }

    tiktok_capi_send_event.delay(
        store_id=str(store.id),
        pixel_id=pixel_id,
        event_name="CompletePayment",
        event_id=event_id,
        event_time=int(datetime.now(UTC).timestamp()),
        # Same reasoning as the Meta test event: TikTok wants a page URL on
        # web-sourced events, and the store origin is the honest source for
        # a synthetic one. Lands as ``page.url`` in the Events API payload.
        event_source_url=store.store_url,
        user_data=synthetic_user_data,
        custom_data={
            "value": 0.01,
            "currency": currency,
            "order_id": event_id,
        },
        test_event_code=request.test_event_code,
        action_source="web",
    )

    logger.info(
        "tiktok_capi_test_event_enqueued store_id=%s event_id=%s test_event_code=%s",
        store.id,
        event_id,
        request.test_event_code,
    )

    return SuccessResponse(
        data=SendTikTokTestEventResponse(
            enqueued=True,
            test_event_code=request.test_event_code,
            queued_event_id=event_id,
        ),
        message="Test event enqueued",
    )


@router.get(
    "/tracking/tiktok/events",
    response_model=SuccessResponse[list[TikTokEventLogEntry]],
    summary="Get recent TikTok Events API events",
    operation_id="get_tiktok_events",
)
async def get_tiktok_events(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
    limit: int = 20,
):
    """Return last N ``tiktok_event_log`` rows, redacted.

    The ``request_payload.user`` sub-object is dropped; only boolean
    presence indicators survive.
    """
    from src.infrastructure.repositories.tiktok_event_log_repository import (
        TikTokEventLogRepository,
    )

    limit = min(max(limit, 1), 100)
    repo = TikTokEventLogRepository(db)
    rows = await repo.recent_for_store(store.id, limit=limit)

    out: list[TikTokEventLogEntry] = []
    for r in rows:
        redacted = dict(r.request_payload or {})
        user = redacted.pop("user", None) or {}
        redacted["user_indicators"] = {
            "had_email": bool(user.get("email")),
            "had_phone": bool(user.get("phone")),
            "had_first_name": bool(user.get("first_name")),
            "had_last_name": bool(user.get("last_name")),
            "had_city": bool(user.get("city")),
            "had_country": bool(user.get("country")),
            "had_zip": bool(user.get("zip_code")),
            "had_external_id": bool(user.get("external_id")),
            "had_ttclid": bool(user.get("ttclid")),
            "had_ttp": bool(user.get("ttp")),
            # See the Meta block above — same diagnostic gap.
            "had_ip": bool(user.get("ip")),
            "had_user_agent": bool(user.get("user_agent")),
        }
        out.append(
            TikTokEventLogEntry(
                id=str(r.id),
                event_id=r.event_id,
                event_name=r.event_name,
                event_time=r.event_time,
                pixel_id=r.pixel_id,
                response_status=r.response_status,
                response_code=r.response_code,
                request_id=r.request_id,
                attempt_count=r.attempt_count,
                last_error=r.last_error,
                sent_at=r.sent_at,
                created_at=r.created_at,
                channel="server",
                request_payload_redacted=redacted,
            )
        )

    return SuccessResponse(data=out, message="Recent TikTok events retrieved")


@router.post(
    "/tracking/tiktok/verify",
    response_model=SuccessResponse[VerifyConnectionResponse],
    summary="Verify the TikTok Pixel against TikTok",
    operation_id="verify_tiktok_tracking_connection",
)
async def verify_tiktok_tracking_connection(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Ask TikTok whether this store's Pixel Code exists on its advertiser.

    ``GET /open_api/v1.3/pixel/list/?advertiser_id=…`` with the merchant's
    Events API token, then look for the configured code in the response.

    TikTok's quirk: HTTP 200 does NOT mean success — the real status is the
    ``code`` field in the body, where 0 means OK. The Events API fanout already
    treats ``code == 0`` as the success signal, and this follows the same rule
    rather than trusting the HTTP status.

    Requires ``advertiser_id``: unlike Meta, TikTok has no endpoint that reads
    a pixel by code alone. Without it we say so instead of guessing.
    """
    import httpx

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    cfg = _tiktok_cfg(store)
    pixel_id = (cfg.get("pixel_id") or "").strip()
    advertiser_id = (cfg.get("advertiser_id") or "").strip()
    if not pixel_id:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error="Save a Pixel Code first.",
            ),
            message="TikTok connection not verified",
        )
    if not advertiser_id:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error=(
                    "Add your TikTok advertiser ID — TikTok can only look a "
                    "Pixel up within an advertiser account."
                ),
            ),
            message="TikTok connection not verified",
        )

    cred = await _get_tiktok_credential(db, store.tenant_id)
    token = ""
    if cred and cred.is_active:
        try:
            sm = get_secrets_manager()
            decrypted = await sm.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            token = decrypted.get("access_token") or ""
        except Exception:
            logger.warning("tiktok_verify_token_decrypt_failed store_id=%s", store.id)
    if not token:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error=(
                    "Add an Events API access token so we can check this "
                    "Pixel with TikTok."
                ),
            ),
            message="TikTok connection not verified",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://business-api.tiktok.com/open_api/v1.3/pixel/list/",
                params={"advertiser_id": advertiser_id},
                headers={"Access-Token": token},
            )
        body = resp.json() if resp.content else {}
    except Exception as exc:
        logger.warning("tiktok_verify_request_failed store_id=%s", store.id)
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error=f"Couldn't reach TikTok: {type(exc).__name__}",
            ),
            message="TikTok connection not verified",
        )

    # code == 0 is TikTok's success signal; HTTP 200 alone means nothing.
    if body.get("code") != 0:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error=body.get("message") or f"TikTok returned HTTP {resp.status_code}",
            ),
            message="TikTok connection not verified",
        )

    pixels = ((body.get("data") or {}).get("pixels")) or []
    match = next(
        (
            p
            for p in pixels
            if isinstance(p, dict)
            and str(p.get("pixel_code") or "").strip() == pixel_id
        ),
        None,
    )
    if match is None:
        return SuccessResponse(
            data=VerifyConnectionResponse(
                verified=False,
                platform="tiktok",
                error=(
                    "TikTok didn't return this Pixel Code for that advertiser "
                    "— check the code, or that the advertiser ID is the one "
                    "that owns it."
                ),
            ),
            message="TikTok connection not verified",
        )

    return SuccessResponse(
        data=VerifyConnectionResponse(
            verified=True,
            platform="tiktok",
            name=match.get("pixel_name"),
        ),
        message="TikTok connection verified",
    )


@router.get(
    "/tracking/tiktok/status",
    response_model=SuccessResponse[TikTokTrackingStatusResponse],
    summary="Get TikTok tracking status",
    operation_id="get_tiktok_tracking_status",
)
async def get_tiktok_tracking_status(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Live status badge data for the TikTok panel."""
    from src.infrastructure.repositories.tiktok_event_log_repository import (
        TikTokEventLogRepository,
    )

    cfg = _tiktok_cfg(store)
    cred = await _get_tiktok_credential(db, store.tenant_id)
    has_token = cred is not None and cred.is_active
    mode = resolve_tiktok_mode(cfg, has_token)

    repo = TikTokEventLogRepository(db)
    recent = await repo.recent_for_store(store.id, limit=20)

    def _is_failed(r) -> bool:
        return (
            r.response_status is None
            or r.response_status >= 400
            or r.response_code not in (0, None)
        )

    failed = sum(1 for r in recent if _is_failed(r))
    failure_rate = (failed / len(recent)) if recent else 0.0

    if mode == "off":
        status_label = "disabled"
    elif not recent:
        status_label = "configured_no_events"
    elif len(recent) >= 5 and sum(1 for r in recent[:5] if _is_failed(r)) == 5:
        status_label = "failing"
    else:
        status_label = "connected"

    return SuccessResponse(
        data=TikTokTrackingStatusResponse(
            status=status_label,
            mode=mode,
            last_validated_at=cred.last_validated_at if cred else None,
            recent_failure_rate=round(failure_rate, 4),
            recent_event_count=len(recent),
        ),
        message="TikTok tracking status retrieved",
    )


@router.get(
    "/tracking/tiktok/report",
    response_model=SuccessResponse[TikTokReportResponse],
    summary="Get TikTok Marketing ad-performance report",
    operation_id="get_tiktok_report",
)
async def get_tiktok_report(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
    days: int = 30,
):
    """Aggregated advertiser-level spend/impressions/conversions for the panel.

    Returns ``connected=false`` when the store has no ``advertiser_id`` on file
    (or no token) — the merchant only gets this after the OAuth flow, since a
    hand-pasted Events API token lacks reporting scope. Reporting failures
    (e.g. missing scope) come back as ``connected=true`` with an ``error``
    string rather than misleading zeros.
    """
    from datetime import timedelta

    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )
    from src.infrastructure.external_services.tiktok.marketing_client import (
        TikTokMarketingClient,
        TikTokMarketingError,
    )

    cfg = _tiktok_cfg(store)
    advertiser_id = cfg.get("advertiser_id")
    cred = await _get_tiktok_credential(db, store.tenant_id)

    if not advertiser_id or cred is None or not cred.is_active:
        return SuccessResponse(
            data=TikTokReportResponse(connected=False, advertiser_id=advertiser_id),
            message="TikTok reporting not connected",
        )

    days = min(max(days, 1), 90)
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    start_s, end_s = start.isoformat(), end.isoformat()

    try:
        sm = get_secrets_manager()
        decrypted = await sm.decrypt(cred.credentials_encrypted, cred.encryption_key_id)
        access_token = decrypted.get("access_token")
    except Exception:
        logger.warning("tiktok_report_token_decrypt_failed store_id=%s", store.id)
        return SuccessResponse(
            data=TikTokReportResponse(
                connected=True,
                advertiser_id=advertiser_id,
                start_date=start_s,
                end_date=end_s,
                error="token_decrypt_failed",
            ),
            message="TikTok reporting error",
        )

    try:
        report = await TikTokMarketingClient().get_advertiser_report(
            access_token=access_token,
            advertiser_id=str(advertiser_id),
            start_date=start_s,
            end_date=end_s,
        )
    except TikTokMarketingError as exc:
        logger.info(
            "tiktok_report_failed store_id=%s error=%s", store.id, str(exc)[:200]
        )
        return SuccessResponse(
            data=TikTokReportResponse(
                connected=True,
                advertiser_id=str(advertiser_id),
                start_date=start_s,
                end_date=end_s,
                error=str(exc)[:200],
            ),
            message="TikTok reporting error",
        )

    return SuccessResponse(
        data=TikTokReportResponse(
            connected=True,
            advertiser_id=str(advertiser_id),
            start_date=start_s,
            end_date=end_s,
            spend=report.spend,
            impressions=report.impressions,
            clicks=report.clicks,
            conversions=report.conversions,
            cost_per_conversion=report.cost_per_conversion,
            ctr=report.ctr,
        ),
        message="TikTok report retrieved",
    )


# ============================================================================
# TikTok Shop sales channel (P7) — connection persistence
# ============================================================================


def _tiktok_shop_cfg(store: Store) -> dict:
    """Read ``store.settings.channels.tiktok_shop`` (or empty)."""
    return ((store.settings or {}).get("channels") or {}).get("tiktok_shop") or {}


@router.put(
    "/channels/tiktok-shop",
    response_model=SuccessResponse[TikTokShopStatusResponse],
    summary="Connect TikTok Shop (persist token + shop)",
    operation_id="connect_tiktok_shop",
)
async def connect_tiktok_shop(
    request: ConnectTikTokShopRequest,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Persist the OAuth token bundle (encrypted) + shop metadata.

    Called by the hub after the OAuth callback returns the token + chosen shop.
    """
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    sm = get_secrets_manager()
    key_id = await sm.get_current_key_id()
    encrypted = await sm.encrypt(
        {
            "access_token": request.access_token,
            "refresh_token": request.refresh_token or "",
        },
        key_id,
    )

    existing = (
        await db.execute(
            _select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.SALES_CHANNEL)
            .where(ServiceCredential.service_name == ServiceName.TIKTOK_SHOP)
        )
    ).scalar_one_or_none()
    if existing:
        existing.credentials_encrypted = encrypted
        existing.encryption_key_id = key_id
        existing.is_active = True
        existing.is_validated = True
        existing.extra_metadata = {"shop_id": request.shop_id}
    else:
        db.add(
            ServiceCredential(
                tenant_id=store.tenant_id,
                service_type=ServiceType.SALES_CHANNEL,
                service_name=ServiceName.TIKTOK_SHOP,
                credentials_encrypted=encrypted,
                encryption_key_id=key_id,
                is_active=True,
                is_validated=True,
                extra_metadata={"shop_id": request.shop_id},
            )
        )
    await db.flush()

    settings_dict: dict = store.settings or {}
    channels = settings_dict.get("channels") or {}
    channels["tiktok_shop"] = {
        "shop_id": request.shop_id,
        "shop_cipher": request.shop_cipher,
        "shop_name": request.shop_name,
        "region": request.region,
        "seller_name": request.seller_name,
        "connected_at": datetime.now(UTC).isoformat(),
    }
    settings_dict["channels"] = channels
    store.settings = settings_dict
    try:
        _flag_modified(store, "settings")
    except Exception:
        pass
    await store_repo.update(store)

    logger.info(
        "tiktok_shop_connected store_id=%s shop_id=%s", store.id, request.shop_id
    )
    cfg = _tiktok_shop_cfg(store)
    return SuccessResponse(
        data=TikTokShopStatusResponse(
            connected=True,
            shop_id=cfg.get("shop_id"),
            shop_name=cfg.get("shop_name"),
            region=cfg.get("region"),
            seller_name=cfg.get("seller_name"),
        ),
        message="TikTok Shop connected",
    )


@router.get(
    "/channels/tiktok-shop",
    response_model=SuccessResponse[TikTokShopStatusResponse],
    summary="TikTok Shop connection status",
    operation_id="get_tiktok_shop_status",
)
async def get_tiktok_shop_status(
    store: Annotated[Store, Depends(get_current_store)],
):
    cfg = _tiktok_shop_cfg(store)
    connected_at = None
    raw = cfg.get("connected_at")
    if raw:
        try:
            connected_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            connected_at = None
    return SuccessResponse(
        data=TikTokShopStatusResponse(
            connected=bool(cfg.get("shop_id")),
            shop_id=cfg.get("shop_id"),
            shop_name=cfg.get("shop_name"),
            region=cfg.get("region"),
            seller_name=cfg.get("seller_name"),
            connected_at=connected_at,
        ),
        message="TikTok Shop status retrieved",
    )


@router.delete(
    "/channels/tiktok-shop",
    response_model=SuccessResponse[TikTokShopStatusResponse],
    summary="Disconnect TikTok Shop",
    operation_id="disconnect_tiktok_shop",
)
async def disconnect_tiktok_shop(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[_AsyncSession, Depends(_get_db)],
):
    """Soft-delete the credential + clear the channel settings."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    cred = (
        await db.execute(
            _select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.SALES_CHANNEL)
            .where(ServiceCredential.service_name == ServiceName.TIKTOK_SHOP)
        )
    ).scalar_one_or_none()
    if cred is not None:
        cred.is_active = False
        await db.flush()

    settings_dict: dict = store.settings or {}
    channels = settings_dict.get("channels") or {}
    if "tiktok_shop" in channels:
        channels.pop("tiktok_shop", None)
        settings_dict["channels"] = channels
        store.settings = settings_dict
        try:
            _flag_modified(store, "settings")
        except Exception:
            pass
        await store_repo.update(store)

    logger.info("tiktok_shop_disconnected store_id=%s", store.id)
    return SuccessResponse(
        data=TikTokShopStatusResponse(connected=False),
        message="TikTok Shop disconnected",
    )
