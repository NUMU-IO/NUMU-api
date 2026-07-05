"""Public storefront checkout-fields config.

URL: GET /storefront/store/{store_id}/checkout-config

The storefront fetches this to render the dynamic checkout form — which
standard fields are enabled/required, plus any custom fields the merchant
has added. No auth.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_network_reputation_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.core.checkout_fields import resolve_config
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.repositories.shopify_repository import (
    NetworkReputationRepository,
)

router = APIRouter()


# Bilingual labels for the payment providers surfaced at checkout. Keys are the
# provider codes used in store.settings["payment"] + the market registry. Any
# provider missing here falls back to a title-cased code so a newly-added
# gateway still renders rather than crashing the endpoint.
_PROVIDER_LABELS: dict[str, tuple[str, str]] = {
    "cod": ("Cash on Delivery", "الدفع عند الاستلام"),
    "paymob": ("Card / Wallet (Paymob)", "بطاقة / محفظة (باي موب)"),
    "paymob_applepay": ("Apple Pay", "Apple Pay"),
    "fawry": ("Fawry", "فوري"),
    "fawaterak": ("Fawaterak", "فواتيرك"),
    "kashier": ("Card (Kashier)", "بطاقة (كاشير)"),
    "kashier_applepay": ("Apple Pay", "Apple Pay"),
    "instapay": ("InstaPay", "انستا باي"),
    "vodafone_cash": ("Vodafone Cash", "فودافون كاش"),
    "bank_transfer": ("Bank Transfer", "تحويل بنكي"),
    "moyasar": ("Card / mada / Apple Pay (Moyasar)", "بطاقة / مدى / Apple Pay (ميسر)"),
    "hyperpay": ("HyperPay", "هايبر باي"),
    "tabby": ("Tabby", "تابي"),
    "tamara": ("Tamara", "تمارا"),
    "stcpay": ("STC Pay", "إس تي سي باي"),
}

# Card-tokenizing gateways. Saved cards ("Pay with •••• 4242") are only
# offered when at least one of these is enabled — the others (COD, InstaPay,
# bank transfer, BNPL) don't produce a reusable card token.
_CARD_TOKEN_PROVIDERS: frozenset[str] = frozenset({"paymob", "kashier", "moyasar"})


@router.get(
    "/checkout-config",
    response_model=SuccessResponse[dict],
    summary="Get public checkout field + payment config",
    operation_id="get_public_checkout_config",
)
async def get_public_checkout_config(
    store_id: Annotated[UUID, Path(description="Store ID")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    db: Annotated[AsyncSession | None, Depends(get_db)] = None,
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))
    config = resolve_config(store.settings)

    # Surface the enabled + market-allowed payment providers so the
    # storefront's payment step renders the right options (e.g. Moyasar
    # for a Saudi store) instead of falling back to a hardcoded default.
    # Mirrors the gating in get_store_payment_methods: a provider shows
    # when it's enabled in settings AND offered in the store's market;
    # outside production we surface merely-enabled (not-yet-configured)
    # providers so merchants can preview their onboarding selections.
    from src.application.services.market_registry import get_market
    from src.config import settings as app_settings

    market = get_market(getattr(store, "country", None))
    allowed_providers = list(market.payment_providers)
    if "cod" not in allowed_providers:
        allowed_providers.append("cod")
    payment_settings = (store.settings or {}).get("payment", {})

    # An enabled gateway must NEVER be hidden just because the market registry
    # doesn't list it for this country (e.g. vodafone_cash / bank_transfer for
    # an EG store — both have merchant enable endpoints + settings defaults but
    # aren't in EGYPT.payment_providers). The market list governs which
    # providers we OFFER during onboarding; a provider the merchant has
    # explicitly switched on is always surfaced at checkout. Non-dict entries
    # like `bank_accounts_count` are skipped by the isinstance guard.
    for provider, cfg in payment_settings.items():
        if (
            isinstance(cfg, dict)
            and cfg.get("enabled")
            and provider not in allowed_providers
        ):
            allowed_providers.append(provider)

    enabled_methods: list[str] = []
    for provider in allowed_providers:
        cfg = payment_settings.get(provider, {})
        if not cfg.get("enabled"):
            continue
        if cfg.get("is_configured") or app_settings.environment != "production":
            enabled_methods.append(provider)
    config["enabled_payment_methods"] = enabled_methods

    # ── Structured payment config (commerce-correctness Phase 1) ───────
    # A richer, storefront-friendly shape alongside the legacy
    # `enabled_payment_methods` list. Built from the SAME merchant payment
    # settings + COD deposit policy that power the merchant payment-settings
    # screen — no invented data. The COD deposit policy lives nested under
    # settings.payment.cod.deposit_policy (see stores/settings.py).
    cod_block = payment_settings.get("cod") or {}
    deposit_raw = cod_block.get("deposit_policy") or {}
    deposit_enabled = bool(deposit_raw.get("enabled", False))
    deposit_gateways = [
        g for g in (deposit_raw.get("allowed_gateways") or []) if isinstance(g, str)
    ]
    cod_enabled = "cod" in enabled_methods

    payment_methods: list[dict] = []
    for provider in enabled_methods:
        label, label_ar = _PROVIDER_LABELS.get(
            provider, (provider.replace("_", " ").title(), provider)
        )
        # A method "requires_deposit" when it's COD and the merchant turned on
        # the deposit-to-confirm policy. Online methods never require a
        # separate deposit — they capture up-front.
        payment_methods.append({
            "code": provider,
            "label": label,
            "label_ar": label_ar,
            "requires_deposit": provider == "cod" and deposit_enabled,
        })

    # Apple Pay ride-along options — surfaced as their own one-tap method when
    # the merchant enabled Apple Pay on that gateway AND the platform master
    # switch is on. They dispatch through the same gateway branch at checkout
    # (paymob*/kashier*), so they're additive to the richer payment_methods
    # shape only (kept out of the provider-level `enabled_payment_methods` list).
    from src.application.services.platform_flags import (
        is_apple_pay_platform_enabled,
    )

    apple_pay_ok = await is_apple_pay_platform_enabled(db)
    for _gw, _code in (("paymob", "paymob_applepay"), ("kashier", "kashier_applepay")):
        _cfg = payment_settings.get(_gw) or {}
        if apple_pay_ok and _gw in enabled_methods and _cfg.get("apple_pay_enabled"):
            _lbl, _lbl_ar = _PROVIDER_LABELS[_code]
            payment_methods.append({
                "code": _code,
                "label": _lbl,
                "label_ar": _lbl_ar,
                "requires_deposit": False,
            })

    currency = (
        store.default_currency.value
        if getattr(store, "default_currency", None)
        else market.default_currency.value
    )

    config["payment_methods"] = payment_methods
    config["cod"] = {
        "enabled": cod_enabled,
        "deposit_required": cod_enabled and deposit_enabled,
        "deposit_gateways": deposit_gateways,
    }
    config["currency"] = currency
    config["saved_cards_enabled"] = any(
        p in _CARD_TOKEN_PROVIDERS for p in enabled_methods
    )

    # When the merchant has cod_trust enabled, phone becomes non-optional
    # at COD checkout — surface this so the storefront form can mark the
    # field required up-front instead of letting the user discover it via
    # a 400 from the checkout endpoint.
    cod_trust = (store.settings or {}).get("cod_trust") or {}
    if isinstance(cod_trust, dict) and cod_trust.get("enabled"):
        std = config.setdefault("standard_fields", {})
        phone_cfg = std.setdefault("phone", {"enabled": True, "required": True})
        phone_cfg["enabled"] = True
        phone_cfg["required"] = True
        # Marker the storefront uses to show a tooltip / explainer.
        phone_cfg["required_reason"] = "cod_trust"

    return SuccessResponse(
        data=config,
        message="Checkout config retrieved",
    )


class CodEligibilityRequest(BaseModel):
    """Pre-flight COD-check input — the buyer's contact phone + optional map pin."""

    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float | None = None
    source: str | None = None


@router.post(
    "/cod-eligibility",
    response_model=SuccessResponse[dict],
    summary="Pre-flight COD availability check for a buyer",
    operation_id="check_cod_eligibility",
)
async def check_cod_eligibility(
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: CodEligibilityRequest,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    network_repo: Annotated[
        NetworkReputationRepository,
        Depends(get_network_reputation_repository),
    ],
):
    """Return whether COD is available for this buyer BEFORE they submit.

    Lets the storefront disable / hide the COD option proactively (offering the
    prepaid fallbacks) instead of letting the buyer reach a hard 403 at order
    submit — the differentiator made graceful. It reuses the exact same
    FSM-backed decision the checkout endpoint enforces, so the pre-flight answer
    matches the final one.

    Privacy: only a coarse ``cod_available`` is returned — never the internal
    reason codes or the network score. The merchant feed gets the detail; the
    buyer just needs to know whether to pay online. Fail-open: any error returns
    ``cod_available=true`` so this never blocks a legitimate purchase.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    cod_trust = (store.settings or {}).get("cod_trust") or {}
    if not (isinstance(cod_trust, dict) and cod_trust.get("enabled")):
        # Feature off — nothing to pre-check; COD is always available.
        return SuccessResponse(
            data={"cod_available": True, "fallback_payment_methods": []},
            message="COD eligibility",
        )

    try:
        from src.application.services.cod_trust_service import (
            LocationSignals,
            check_customer_trust,
        )

        decision = await check_customer_trust(
            phone=body.phone,
            store_settings=store.settings,
            network_repo=network_repo,
            location=LocationSignals(
                latitude=body.latitude,
                longitude=body.longitude,
                accuracy=body.accuracy,
                source=body.source,
            ),
        )
        available = decision.allowed
    except Exception:  # noqa: BLE001 — fraud filtering never blocks on error
        available = True

    return SuccessResponse(
        data={
            "cod_available": available,
            # Mirror the checkout endpoint's 403 fallback list so the storefront
            # offers the same prepaid options it would after a hard block.
            "fallback_payment_methods": []
            if available
            else ["paymob_card", "paymob_wallet"],
        },
        message="COD eligibility",
    )
