"""Partner shipping apps as carriers: ``app:<app-slug>`` next to ``bosta``.

A shipping app whose manifest declares a ``carrier`` block plugs into the
carrier layer as one more :class:`ShippingProvider`. NUMU POSTs to the
partner's URLs, signed exactly like webhook deliveries
(``X-NUMU-Signature-V1`` with the app's client secret), and the partner
pushes tracking back with its app token through
``POST /stores/{id}/shipments/carrier-events``.

The registry stays static; these carriers come from the store's installed
apps, so every lookup is per store.

Checkout rule: a partner's rates are best effort. A timeout, an error or a
response that fails validation omits that carrier's rates, never the
checkout.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.app_billing import is_entitled
from src.application.services.app_manifest import _https
from src.application.services.app_tokens import read_client_secret
from src.application.services.partner_program import partner_apps_enabled
from src.application.services.webhook_delivery_service import WebhookDeliveryService
from src.core.entities.app import AppStatus
from src.core.interfaces.services.shipping_provider import (
    CarrierApiError,
    NotSupportedByCarrier,
    ProviderCapabilities,
    ShipmentLabel,
    ShippingProvider,
)
from src.core.logging import get_logger
from src.core.url_guard import assert_webhook_target
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)

logger = get_logger(__name__)

APP_CARRIER_PREFIX = "app:"
RATES_TIMEOUT = 3.0
CALL_TIMEOUT = 10.0
RATES_CACHE_SECONDS = 60

_cache = RedisCacheService()
_transport: httpx.AsyncBaseTransport | None = None


def is_app_carrier(slug: str | None) -> bool:
    return bool(slug) and slug.startswith(APP_CARRIER_PREFIX)


def app_carrier_slug(app_slug: str) -> str:
    return APP_CARRIER_PREFIX + app_slug


# ─── What a partner may answer ─────────────────────────────────────


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RateQuote(_Answer):
    service_code: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(ge=0, le=10_000_000)
    currency: str = Field(default="EGP", min_length=3, max_length=3)
    days_min: int = Field(default=2, ge=0, le=60)
    days_max: int = Field(default=5, ge=0, le=60)
    cod_supported: bool = True


class RatesAnswer(_Answer):
    rates: list[RateQuote] = Field(max_length=20)


class ShipmentAnswer(_Answer):
    tracking_number: str = Field(min_length=1, max_length=100)
    carrier_shipment_id: str | None = Field(default=None, max_length=100)
    label_url: str | None = None
    tracking_url: str | None = None

    @field_validator("label_url", "tracking_url")
    @classmethod
    def _urls(cls, v: str | None) -> str | None:
        return _https(v) if v else None


# ─── Signed calls ──────────────────────────────────────────────────


def signed_headers(secret: str, body: bytes, event: str) -> dict[str, str]:
    """The webhook delivery headers, so one verifier covers both."""
    sent_at = int(time.time())
    return {
        "Content-Type": "application/json",
        "X-NUMU-Signature": WebhookDeliveryService._sign(secret, body),
        "X-NUMU-Signature-V1": WebhookDeliveryService._sign_v1(secret, body, sent_at),
        "X-NUMU-Timestamp": str(sent_at),
        "X-NUMU-Event": event,
        "X-NUMU-Delivery": str(uuid4()),
    }


async def _post(
    url: str, secret: str, event: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """POST a signed JSON body; the whole call, DNS check included, is capped."""
    body = json.dumps(payload, default=str).encode()

    async def call() -> httpx.Response:
        await asyncio.to_thread(assert_webhook_target, url)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=_transport
        ) as client:
            return await client.post(
                url, content=body, headers=signed_headers(secret, body, event)
            )

    response = await asyncio.wait_for(call(), timeout)
    if response.status_code >= 300:
        raise CarrierApiError(response.status_code, carrier=event)
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("partner answered with a non-object body")
    return data


# ─── The provider ──────────────────────────────────────────────────


@dataclass(kw_only=True)
class PartnerCarrier(ShippingProvider):
    carrier: str
    store_id: UUID
    name: dict[str, str]
    icon_url: str | None
    config: dict[str, Any]
    secret: str

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_cod=bool(self.config.get("cod", True)),
            supports_labels=bool(self.config.get("labels")),
            supports_cancel=bool(self.config.get("cancel_url")),
            supports_live_rates=bool(self.config.get("rates_url")),
            supports_webhooks=True,
        )

    def supports(self, operation: str) -> bool:
        return operation in ("create_shipment", "validate_address") or (
            operation == "cancel_shipment" and bool(self.config.get("cancel_url"))
        )

    async def create_shipment(
        self,
        *,
        from_address,
        to_address,
        parcel,
        rate_id,
        cod_amount=None,
        order_reference=None,
        notes=None,
    ) -> ShipmentLabel:
        service_code = rate_id.removeprefix(f"{self.carrier}_")
        data = await _post(
            self.config["create_shipment_url"],
            self.secret,
            "carrier.shipment.create",
            {
                "store_id": str(self.store_id),
                "order_reference": order_reference,
                "service_code": service_code,
                "from": asdict(from_address),
                "to": asdict(to_address),
                "parcel": asdict(parcel),
                "cod_amount_cents": cod_amount,
                "notes": notes,
            },
            CALL_TIMEOUT,
        )
        answer = ShipmentAnswer.model_validate(data)
        return ShipmentLabel(
            label_url=answer.label_url or "",
            tracking_number=answer.tracking_number,
            carrier=self.carrier,
            service=service_code,
            carrier_shipment_id=answer.carrier_shipment_id,
            tracking_url=answer.tracking_url,
        )

    async def cancel_shipment(self, tracking_number: str) -> bool:
        if not self.config.get("cancel_url"):
            raise NotSupportedByCarrier(self.carrier, "cancelling shipments")
        await _post(
            self.config["cancel_url"],
            self.secret,
            "carrier.shipment.cancel",
            {"store_id": str(self.store_id), "tracking_number": tracking_number},
            CALL_TIMEOUT,
        )
        return True

    async def track_shipment(self, carrier: str, tracking_number: str):
        raise NotSupportedByCarrier(self.carrier, "tracking lookups")

    def catalog_entry(self) -> dict[str, Any]:
        """The hub carrier-catalog shape; installed means connected."""
        caps = self.capabilities
        return {
            "slug": self.carrier,
            "name_en": self.name.get("en") or self.carrier,
            "name_ar": self.name.get("ar") or self.name.get("en") or self.carrier,
            "tier": "app",
            "brand_color": None,
            "is_default": False,
            "is_selectable": True,
            "tracking_url_template": None,
            "capabilities": caps.as_dict(),
            "can_verify": False,
            "credential_fields": [],
            "supported_operations": [
                op
                for op in ("create_shipment", "cancel_shipment", "print_awb")
                if self.supports(op) or (op == "print_awb" and caps.supports_labels)
            ],
            "icon_url": self.icon_url,
            "status": {
                "is_configured": True,
                "enabled": True,
                "verified": True,
                "verified_at": None,
                "verification_error": None,
                "last_configured": None,
                "auto_create_shipment": False,
            },
        }


# ─── Lookup ────────────────────────────────────────────────────────


async def installed_carriers(
    db: AsyncSession, store_id: UUID, app_slug: str | None = None
) -> list[PartnerCarrier]:
    """The store's live shipping apps that declare a carrier."""
    query = (
        select(AppModel, AppInstallationModel)
        .join(AppInstallationModel, AppInstallationModel.app_id == AppModel.id)
        .where(
            AppInstallationModel.store_id == store_id,
            AppInstallationModel.is_enabled.is_(True),
            AppInstallationModel.status == "active",
            AppModel.category == "shipping",
        )
    )
    if app_slug is not None:
        query = query.where(AppModel.slug == app_slug)
    rows = (await db.execute(query)).all()
    out: list[PartnerCarrier] = []
    for app, installation in rows:
        config = ((app.manifest or {}).get("app") or {}).get("carrier")
        if not config or app.status == AppStatus.SUSPENDED:
            continue
        if app.developer_id is not None and not await partner_apps_enabled(db):
            continue
        if not await is_entitled(db, installation, app):
            continue
        secret = await read_client_secret(db, app.id)
        if not secret:
            continue
        names = (app.manifest or {}).get("app_locales") or {}
        out.append(
            PartnerCarrier(
                carrier=app_carrier_slug(app.slug),
                store_id=store_id,
                name={
                    lang: (names.get(lang) or {}).get("name") or app.name
                    for lang in ("ar", "en")
                },
                icon_url=app.icon_url,
                config=config,
                secret=secret,
            )
        )
    return out


async def load_partner_carrier(
    db: AsyncSession | None, store_id: UUID | None, carrier: str
) -> PartnerCarrier | None:
    if db is None or store_id is None or not is_app_carrier(carrier):
        return None
    found = await installed_carriers(
        db, store_id, carrier.removeprefix(APP_CARRIER_PREFIX)
    )
    return found[0] if found else None


# ─── Checkout rates ────────────────────────────────────────────────


async def _fetch_rates(
    partner: PartnerCarrier, payload: dict[str, Any]
) -> list[RateQuote]:
    if not partner.config.get("rates_url"):
        return []
    try:
        data = await _post(
            partner.config["rates_url"],
            partner.secret,
            "carrier.rates",
            payload,
            RATES_TIMEOUT,
        )
        return RatesAnswer.model_validate(data).rates
    except Exception as exc:  # noqa: BLE001 — a partner never breaks checkout
        logger.warning(
            "partner_rates_omitted",
            carrier=partner.carrier,
            error=type(exc).__name__,
        )
        return []


async def quote_rates(
    db: AsyncSession | None,
    store_id: UUID,
    carriers: list[str],
    *,
    governorate_code: str,
    subtotal_cents: int,
    weight_g: int,
    cod: bool,
) -> dict[str, dict[str, RateQuote]]:
    """``{carrier: {service_code: quote}}``; cached briefly per cart shape
    so the options call and the checkout re-check see the same price."""
    payload = {
        "store_id": str(store_id),
        "destination": {"governorate_code": governorate_code, "country": "EG"},
        "cart": {"subtotal_cents": subtotal_cents, "weight_g": weight_g},
        "cod": cod,
    }
    keys = {
        c: "partner_rates:"
        + hashlib.sha256(json.dumps([c, payload], sort_keys=True).encode()).hexdigest()
        for c in carriers
    }
    found: dict[str, list[RateQuote]] = {}
    missing: list[PartnerCarrier] = []
    for carrier in carriers:
        cached = await _cache.get(keys[carrier])
        if isinstance(cached, list):
            found[carrier] = [RateQuote.model_validate(q) for q in cached]
            continue
        partner = await load_partner_carrier(db, store_id, carrier)
        if partner is None:
            found[carrier] = []
        else:
            missing.append(partner)
    answers = await asyncio.gather(*(_fetch_rates(p, payload) for p in missing))
    for partner, quotes in zip(missing, answers, strict=True):
        found[partner.carrier] = quotes
        await _cache.set(
            keys[partner.carrier],
            [q.model_dump() for q in quotes],
            expire=RATES_CACHE_SECONDS,
        )
    return {c: {q.service_code: q for q in qs} for c, qs in found.items()}
