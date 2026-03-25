"""J&T Express shipping service implementation for Egyptian market.

J&T Express is a global logistics company with operations in Egypt.
This service integrates with the J&T Express API for shipment creation,
tracking, and rate calculation.

API Documentation: https://openapi.jtexpress-eg.com/
"""

import base64
import hashlib
import hmac
import json
import logging

import httpx

from src.config import settings
from src.core.interfaces.services.shipping_service import (
    IShippingService,
    Parcel,
    ShipmentLabel,
    ShippingAddress,
    ShippingRate,
    TrackingEvent,
    TrackingInfo,
)

logger = logging.getLogger(__name__)


class JTShippingService(IShippingService):
    """J&T Express shipping service for Egyptian market.

    Features:
    - Standard and express delivery across Egypt
    - COD (Cash on Delivery) support
    - Real-time tracking
    - Return shipment handling
    """

    def __init__(
        self,
        api_key: str | None = None,
        customer_code: str | None = None,
        base_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.jt_api_key
        self.customer_code = customer_code or settings.jt_customer_code
        self.base_url = (base_url or settings.jt_base_url).rstrip("/")
        self.webhook_secret = webhook_secret or settings.jt_webhook_secret

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        if not self.api_key:
            raise ValueError("J&T Express API key not configured")
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "apiAccount": self.customer_code or "",
            "digest": self.api_key,
        }

    def _default_rates(self, to_address: ShippingAddress) -> list[ShippingRate]:
        """Return default rates when API is unavailable."""
        return [
            ShippingRate(
                carrier="jt",
                service="standard",
                rate_id="jt_standard",
                amount=4500,  # 45 EGP default
                currency="EGP",
                estimated_days=3,
            )
        ]

    async def get_rates(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
    ) -> list[ShippingRate]:
        """Get shipping rates from J&T Express."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/order/queryFreight",
                    json={
                        "customerCode": self.customer_code,
                        "senderAddr": from_address.city,
                        "receiverAddr": to_address.city,
                        "weight": str(parcel.weight),
                        "length": str(parcel.length),
                        "width": str(parcel.width),
                        "height": str(parcel.height),
                    },
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "J&T rates failed: %s %s", resp.status_code, resp.text
                    )
                    return self._default_rates(to_address)

                data = resp.json()
                if data.get("code") != "1":
                    logger.warning("J&T rates API error: %s", data.get("msg"))
                    return self._default_rates(to_address)

                freight = data.get("data", {})
                amount = int(float(freight.get("totalPrice", 45)) * 100)
                return [
                    ShippingRate(
                        carrier="jt",
                        service="standard",
                        rate_id="jt_standard",
                        amount=amount,
                        currency="EGP",
                        estimated_days=freight.get("estimatedDays", 3),
                    )
                ]
        except Exception as e:
            logger.warning("J&T get_rates error: %s", e)
            return self._default_rates(to_address)

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
        """Create a shipment via J&T Express API."""
        payload = {
            "customerCode": self.customer_code,
            "orderType": "1",  # 1 = pickup
            "serviceType": "1",  # 1 = standard
            "sender": {
                "name": from_address.name,
                "phone": from_address.phone or "",
                "address": f"{from_address.street1} {from_address.street2 or ''}".strip(),
                "city": from_address.city,
            },
            "receiver": {
                "name": to_address.name,
                "phone": to_address.phone or "",
                "address": f"{to_address.street1} {to_address.street2 or ''}".strip(),
                "city": to_address.city,
            },
            "weight": str(parcel.weight),
            "itemsValue": str((cod_amount / 100) if cod_amount else 0),
            "remark": notes or "",
            "txlogisticId": order_reference or "",
            "payType": "PP" if not cod_amount or cod_amount <= 0 else "CC",
        }

        if cod_amount and cod_amount > 0:
            payload["goodsValue"] = str(cod_amount / 100)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/order/create",
                json=payload,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if resp.status_code not in (200, 201):
                logger.error("J&T shipment creation failed: %s", resp.text)
                raise ValueError(f"Failed to create J&T shipment: {resp.text}")

            data = resp.json()
            if data.get("code") != "1":
                logger.error("J&T shipment API error: %s", data.get("msg"))
                raise ValueError(
                    f"J&T shipment creation error: {data.get('msg', 'Unknown error')}"
                )

            order_data = data.get("data", {})
            bill_code = (
                order_data.get("billCode") or order_data.get("txlogisticId") or ""
            )

            return ShipmentLabel(
                label_url=order_data.get("labelUrl") or "",
                tracking_number=bill_code,
                carrier="jt",
                service="standard",
            )

    async def track_shipment(
        self,
        carrier: str,
        tracking_number: str,
    ) -> TrackingInfo:
        """Track a J&T Express shipment."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/track/query",
                json={
                    "billCodes": tracking_number,
                },
                headers=self._get_headers(),
                timeout=30.0,
            )
            if resp.status_code != 200:
                raise ValueError(f"J&T tracking failed: {resp.text}")

            data = resp.json()
            if data.get("code") != "1":
                raise ValueError(
                    f"J&T tracking error: {data.get('msg', 'Unknown error')}"
                )

            tracks = data.get("data", [])
            if not tracks:
                return TrackingInfo(
                    carrier="jt",
                    tracking_number=tracking_number,
                    status="unknown",
                    events=[],
                )

            track = tracks[0] if isinstance(tracks, list) else tracks
            details = track.get("details", [])
            events = []
            for detail in details:
                events.append(
                    TrackingEvent(
                        status=detail.get("scanType", ""),
                        description=detail.get("desc", ""),
                        location=detail.get("scanCity"),
                        timestamp=detail.get("scanTime", ""),
                    )
                )

            return TrackingInfo(
                carrier="jt",
                tracking_number=tracking_number,
                status=track.get("lastStatus", "unknown"),
                events=events,
                estimated_delivery=None,
            )

    async def validate_address(
        self,
        address: ShippingAddress,
    ) -> tuple[bool, ShippingAddress | None]:
        """Validate address (basic validation for J&T)."""
        is_valid = bool(address.city and address.street1 and address.phone)
        return is_valid, address if is_valid else None

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify J&T Express webhook signature."""
        if not self.webhook_secret:
            return json.loads(payload)
        expected = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            return json.loads(payload)
        return None


async def get_jt_service_for_store(
    store_settings: dict | None = None,
) -> JTShippingService:
    """Get a JTShippingService configured with per-store credentials.

    Falls back to global env vars if no per-store credentials are configured.
    """
    if store_settings:
        shipping = (store_settings or {}).get("shipping", {}).get("jt", {})
        encrypted_creds = shipping.get("encrypted_credentials")
        key_id = shipping.get("encryption_key_id")
        if encrypted_creds and key_id:
            from src.infrastructure.external_services.secrets.secrets_manager import (
                get_secrets_manager,
            )

            secrets_mgr = get_secrets_manager()
            cred_data = await secrets_mgr.decrypt(
                base64.b64decode(encrypted_creds), key_id
            )
            return JTShippingService(
                api_key=cred_data.get("api_key"),
                customer_code=cred_data.get("customer_code"),
                webhook_secret=cred_data.get("webhook_secret"),
                base_url=settings.jt_base_url,
            )
    # Fallback to global settings
    return JTShippingService()
