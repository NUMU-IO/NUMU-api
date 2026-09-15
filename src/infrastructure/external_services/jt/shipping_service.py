"""J&T Express Egypt on the JMS open platform.

Docs: https://open.jtjms-eg.com/#/apiDoc/basic

Every call is a form post of ``bizContent`` (a JSON string) with headers
``apiAccount``, ``timestamp`` and ``digest = base64(md5(bizContent + privateKey))``.
Order calls also carry a business ``digest`` built from the agreement
customer's code and password.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import replace
from typing import Any

import httpx

from src.config import settings
from src.core.interfaces.services.shipping_provider import CarrierApiError
from src.core.interfaces.services.shipping_service import (
    IShippingService,
    Parcel,
    ShipmentLabel,
    ShippingAddress,
    ShippingRate,
    TrackingEvent,
    TrackingInfo,
    parse_carrier_timestamp,
)
from src.core.logging import get_logger
from src.core.value_objects.geography import resolve_governorate
from src.infrastructure.webhooks.carrier_parsers import decode_webhook_body

logger = get_logger(__name__)

SANDBOX_BASE_URL = "https://demoopenapi.jtjms-eg.com/webopenplatformapi/api"
PASSWORD_SALT = "jadada236t2"
AUTH_ERROR_CODES = frozenset({"145003010", "145003030", "145003031"})
NO_LOCATION_PERMISSION = "145003012"
NOT_FOUND = "999002000"
LOCATION_TTL_SECONDS = 24 * 3600

_location_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
_ARABIC_FOLD = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ة": "ه",
    "ى": "ي",
    "ـ": None,
})


def md5_base64(text: str) -> str:
    digest = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).digest()
    return base64.b64encode(digest).decode()


def _fold(text: Any) -> str:
    text = re.sub(r"[ً-ْ]", "", str(text or "")).translate(_ARABIC_FOLD)
    return re.sub(r"\W+", "", text.lower())


def _contains(needle: str, haystack: str) -> bool:
    return len(needle) > 2 and needle in haystack


def _local_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "").removeprefix("00")
    if digits.startswith("20") and len(digits) == 12:
        return "0" + digits[2:]
    return digits


class JTShippingService(IShippingService):
    """J&T Express Egypt: booking, cancel, labels, tracking, status push."""

    def __init__(
        self,
        api_account: str | None = None,
        private_key: str | None = None,
        customer_code: str | None = None,
        customer_password: str | None = None,
        sender_name: str | None = None,
        sender_phone: str | None = None,
        sender_governorate: str | None = None,
        sender_city: str | None = None,
        sender_area: str | None = None,
        sender_street: str | None = None,
        environment: str | None = None,
        base_url: str | None = None,
        **_legacy: Any,
    ) -> None:
        self.api_account = api_account or ""
        self.private_key = private_key or ""
        self.customer_code = customer_code or ""
        self.customer_password = customer_password or ""
        self.sender = ShippingAddress(
            name=sender_name or "",
            street1=sender_street or "",
            street2=sender_area,
            city=sender_city or "",
            state=sender_governorate,
            country="Egypt",
            phone=sender_phone,
        )
        sandbox = (environment or "").strip().lower() == "sandbox"
        default_url = SANDBOX_BASE_URL if sandbox else settings.jt_base_url
        self.base_url = (base_url or default_url).rstrip("/")

    def _business_digest(self) -> str:
        cipher = hashlib.md5(
            f"{self.customer_password}{PASSWORD_SALT}".encode(), usedforsecurity=False
        ).hexdigest()
        return md5_base64(f"{self.customer_code}{cipher.upper()}{self.private_key}")

    async def _post(self, path: str, biz: dict[str, Any]) -> Any:
        if not (self.api_account and self.private_key):
            raise CarrierApiError(401, "J&T Express is not connected", carrier="jt")

        content = json.dumps(biz, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "apiAccount": self.api_account,
            "digest": md5_base64(content + self.private_key),
            "timestamp": str(int(time.time() * 1000)),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/{path}",
                data={"bizContent": content},
                headers=headers,
            )

        if resp.status_code != 200:
            raise CarrierApiError(
                resp.status_code,
                f"J&T HTTP {resp.status_code}: {resp.text[:300]}",
                carrier="jt",
            )
        body = resp.json()
        code = str(body.get("code", ""))
        if code != "1" and str(body.get("msg", "")).lower() != "success":
            logger.warning("jt_api_error", path=path, code=code, msg=body.get("msg"))
            raise CarrierApiError(
                401 if code in AUTH_ERROR_CODES else 422,
                f"J&T {code}: {body.get('msg') or 'unknown error'}",
                carrier="jt",
            )
        return body.get("data")

    async def get_cities(self) -> list[dict[str, Any]]:
        """J&T's Egypt province/city/area tree; [] when the account lacks that API."""
        key = (self.base_url, self.api_account, self.private_key)
        cached = _location_cache.get(key)
        if cached and time.monotonic() - cached[0] < LOCATION_TTL_SECONDS:
            return cached[1]
        try:
            rows = (
                await self._post("location/getLocation", {"countryCode": "EGY"}) or []
            )
        except CarrierApiError as e:
            # J&T answers 145003012 before checking the key; book with plain names.
            if not str(e).startswith(f"J&T {NO_LOCATION_PERMISSION}:"):
                raise
            rows = []
        # ponytail: per-process cache; move to Redis if workers multiply.
        _location_cache[key] = (time.monotonic(), rows)
        return rows

    async def _locate(self, address: ShippingAddress) -> dict[str, str]:
        rows = await self.get_cities()
        governorate = resolve_governorate(address.state or "") or resolve_governorate(
            address.city or ""
        )
        if not rows:
            return {
                "prov": governorate.name_ar if governorate else address.state or "",
                "city": address.city or "",
                "area": address.street2 or address.city or "",
            }
        if governorate:
            names = {_fold(governorate.name_ar), _fold(governorate.name_en)}
            rows = [r for r in rows if _fold(r.get("prov")) in names] or rows

        city = _fold(address.city)
        text = _fold(" ".join(filter(None, [address.street1, address.street2])))
        # ponytail: name matching against J&T's tree; a city with no named area
        # takes its first area. Upgrade: let checkout pick from the J&T tree.
        match = (
            next((r for r in rows if city and _fold(r.get("area")) == city), None)
            or next(
                (
                    r
                    for r in rows
                    if city
                    and _fold(r.get("city")) == city
                    and _contains(_fold(r.get("area")), text)
                ),
                None,
            )
            or next((r for r in rows if city and _fold(r.get("city")) == city), None)
            or next((r for r in rows if _contains(_fold(r.get("area")), text)), None)
        )
        if match is None:
            raise CarrierApiError(
                422,
                f"J&T does not recognise the city '{address.city}' "
                f"({address.state or 'no governorate'}). Use the city or area "
                f"name as J&T lists it.",
                carrier="jt",
            )
        return {k: str(match.get(k) or "") for k in ("prov", "city", "area")}

    @staticmethod
    def _party(address: ShippingAddress, place: dict[str, str]) -> dict[str, Any]:
        street = " ".join(filter(None, [address.street1, address.street2]))[:200]
        phone = _local_phone(address.phone)
        return {
            "name": address.name[:50],
            "mobile": phone,
            "phone": phone,
            "countryCode": "EGY",
            **place,
            "street": street,
            "address": street,
        }

    async def create_shipment(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
        rate_id: str | None = None,
        cod_amount: int | None = None,
        order_reference: str | None = None,
        notes: str | None = None,
    ) -> ShipmentLabel:
        sender = replace(self.sender, name=self.sender.name or from_address.name)
        biz: dict[str, Any] = {
            "customerCode": self.customer_code,
            "digest": self._business_digest(),
            "txlogisticId": f"{order_reference or 'NUMU'}-{secrets.token_hex(3).upper()}",
            "expressType": "EZ",
            "orderType": "2",
            "serviceType": "01",
            "deliveryType": "04",
            "payType": "PP_PM",
            "goodsType": "ITN16",
            "operateType": 1,
            "totalQuantity": 1,
            "weight": f"{max(parcel.weight, 0.01):.2f}",
            "sender": self._party(sender, await self._locate(sender)),
            "receiver": self._party(to_address, await self._locate(to_address)),
            "remark": (notes or "")[:200],
        }
        if cod_amount and cod_amount > 0:
            biz["itemsValue"] = f"{cod_amount / 100:.2f}"
            biz["priceCurrency"] = "EGP"

        data = await self._post("order/addOrder", biz) or {}
        if not data.get("billCode"):
            raise CarrierApiError(
                502, "J&T accepted the order but returned no waybill", carrier="jt"
            )
        return ShipmentLabel(
            label_url="",
            tracking_number=data["billCode"],
            carrier="jt",
            service="standard",
            carrier_shipment_id=data.get("txlogisticId") or biz["txlogisticId"],
        )

    async def verify_credentials(self) -> None:
        """Cancel an order that doesn't exist.

        J&T checks the API account, private key and customer password before
        looking the order up, so "not found" proves all three.
        """
        try:
            await self.cancel_shipment("NUMU-CREDENTIAL-CHECK")
        except CarrierApiError as e:
            if not str(e).startswith(f"J&T {NOT_FOUND}:"):
                raise

    async def cancel_shipment(self, carrier_shipment_id: str) -> bool:
        """Cancel by J&T's customer order number (txlogisticId)."""
        await self._post(
            "order/cancelOrder",
            {
                "customerCode": self.customer_code,
                "digest": self._business_digest(),
                "orderType": "2",
                "txlogisticId": carrier_shipment_id,
                "reason": "Cancelled by merchant",
            },
        )
        return True

    async def print_awb(self, tracking_number: str) -> bytes:
        data = await self._post(
            "order/printOrder",
            {
                "customerCode": self.customer_code,
                "digest": self._business_digest(),
                "billCode": tracking_number,
                "printSize": 0,
                "printCod": 1,
            },
        )
        return base64.b64decode((data or {}).get("base64EncodeContent") or "")

    async def track_shipment(self, carrier: str, tracking_number: str) -> TrackingInfo:
        data = await self._post("logistics/trace", {"billCodes": tracking_number}) or []
        track = next((t for t in data if t.get("billCode") == tracking_number), None)
        details = (track or {}).get("details") or []
        if not details:
            return TrackingInfo(
                carrier="jt",
                tracking_number=tracking_number,
                status="unknown",
                events=[],
            )

        latest = max(details, key=lambda d: str(d.get("scanTime") or ""))
        return TrackingInfo(
            carrier="jt",
            tracking_number=tracking_number,
            status=str(
                latest.get("scanType") or latest.get("scanTypeCode") or "unknown"
            ),
            events=[
                TrackingEvent(
                    status=str(d.get("scanType") or ""),
                    description=str(d.get("desc") or ""),
                    location=d.get("scanNetworkCity") or d.get("scanNetworkName"),
                    timestamp=parse_carrier_timestamp(d.get("scanTime")),
                )
                for d in details
            ],
        )

    async def get_rates(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
    ) -> list[ShippingRate]:
        """No live quotes: the registry declares supports_live_rates=False."""
        return []

    async def validate_address(
        self,
        address: ShippingAddress,
    ) -> tuple[bool, ShippingAddress | None]:
        is_valid = bool(address.city and address.street1 and address.phone)
        return is_valid, address if is_valid else None

    def verify_webhook_signature(self, payload: bytes, signature: str) -> dict | None:
        """J&T signs pushes like requests: digest = base64(md5(bizContent + privateKey))."""
        body = decode_webhook_body(payload)
        content = body.get("bizContent") if isinstance(body, dict) else None
        if not (content and signature and self.private_key):
            return None
        expected = md5_base64(content + self.private_key)
        if not hmac.compare_digest(expected, signature.strip()):
            return None
        return json.loads(content)


async def get_jt_service_for_store(
    store_settings: dict | None = None,
) -> JTShippingService:
    from src.application.services.carrier_credentials import load_credentials

    return JTShippingService(**(await load_credentials(store_settings, "jt") or {}))
