"""Mylerz shipping service implementation for Egyptian market.

Mylerz is an Egyptian logistics and last-mile delivery company.
This service integrates with the Mylerz API for shipment creation,
tracking, and rate calculation.

API Documentation: https://api.mylerz.com/
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


class MylerzShippingService(IShippingService):
    """Mylerz shipping service for Egyptian market.

    Features:
    - Standard and express delivery across Egypt
    - COD (Cash on Delivery) support
    - Real-time tracking
    - Return shipment handling
    """

    def __init__(
        self,
        api_key: str | None = None,
        merchant_id: str | None = None,
        base_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.mylerz_api_key
        self.merchant_id = merchant_id or settings.mylerz_merchant_id
        self.base_url = (base_url or settings.mylerz_base_url).rstrip("/")
        self.webhook_secret = webhook_secret or settings.mylerz_webhook_secret

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        if not self.api_key:
            raise ValueError("Mylerz API key not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _default_rates(self, to_address: ShippingAddress) -> list[ShippingRate]:
        """Return default rates when API is unavailable."""
        return [
            ShippingRate(
                carrier="mylerz",
                service="standard",
                rate_id="mylerz_standard",
                amount=5000,  # 50 EGP default
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
        """Get shipping rates from Mylerz."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/Shipment/GetRates",
                    json={
                        "from_city": from_address.city,
                        "to_city": to_address.city,
                        "weight": parcel.weight,
                        "cod_amount": 0,
                    },
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Mylerz rates failed: %s %s", resp.status_code, resp.text
                    )
                    return self._default_rates(to_address)

                data = resp.json()
                rates = []
                for rate in data.get("rates", [data]):
                    rates.append(
                        ShippingRate(
                            carrier="mylerz",
                            service=rate.get("service_type", "standard"),
                            rate_id=f"mylerz_{rate.get('service_type', 'standard')}",
                            amount=int(float(rate.get("price", 50)) * 100),
                            currency="EGP",
                            estimated_days=rate.get("estimated_days", 3),
                        )
                    )
                return rates or self._default_rates(to_address)
        except Exception as e:
            logger.warning("Mylerz get_rates error: %s", e)
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
        """Create a shipment via Mylerz API."""
        payload = {
            "MerchantId": self.merchant_id,
            "ConsigneeName": to_address.name,
            "ConsigneePhone": to_address.phone,
            "ConsigneeAddress": f"{to_address.street1} {to_address.street2 or ''}".strip(),
            "ConsigneeCity": to_address.city,
            "PackageWeight": parcel.weight,
            "ServiceType": "DELIVERY",
            "PaymentType": "COD" if cod_amount and cod_amount > 0 else "PREPAID",
            "CODAmount": (cod_amount / 100) if cod_amount else 0,
            "Reference": order_reference or "",
            "Notes": notes or "",
            "NumberOfPieces": 1,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/Shipment/CreateShipment",
                json=payload,
                headers=self._get_headers(),
                timeout=30.0,
            )
            if resp.status_code not in (200, 201):
                logger.error("Mylerz shipment creation failed: %s", resp.text)
                raise ValueError(f"Failed to create Mylerz shipment: {resp.text}")

            data = resp.json()
            barcode = (
                data.get("Barcode")
                or data.get("barcode")
                or data.get("tracking_number", "")
            )

            return ShipmentLabel(
                label_url=data.get("AWBUrl") or data.get("label_url") or "",
                tracking_number=barcode,
                carrier="mylerz",
                service="standard",
            )

    async def track_shipment(
        self,
        carrier: str,
        tracking_number: str,
    ) -> TrackingInfo:
        """Track a Mylerz shipment."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Shipment/TrackShipmentByBarcode/{tracking_number}",
                headers=self._get_headers(),
                timeout=30.0,
            )
            if resp.status_code != 200:
                raise ValueError(f"Mylerz tracking failed: {resp.text}")

            data = resp.json()
            events = []
            for log_entry in data.get("TrackingLogs", []):
                events.append(
                    TrackingEvent(
                        status=log_entry.get("Status", ""),
                        description=log_entry.get("Description", ""),
                        location=log_entry.get("Location"),
                        timestamp=log_entry.get("Date", ""),
                    )
                )

            return TrackingInfo(
                carrier="mylerz",
                tracking_number=tracking_number,
                status=data.get("CurrentStatus", "unknown"),
                events=events,
                estimated_delivery=data.get("EstimatedDelivery"),
            )

    async def validate_address(
        self,
        address: ShippingAddress,
    ) -> tuple[bool, ShippingAddress | None]:
        """Validate address (basic validation for Mylerz)."""
        is_valid = bool(address.city and address.street1 and address.phone)
        return is_valid, address if is_valid else None

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Mylerz webhook signature."""
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


async def get_mylerz_service_for_store(
    store_settings: dict | None = None,
) -> MylerzShippingService:
    """Get a MylerzShippingService configured with per-store credentials.

    Falls back to global env vars if no per-store credentials are configured.
    """
    if store_settings:
        shipping = (store_settings or {}).get("shipping", {}).get("mylerz", {})
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
            return MylerzShippingService(
                api_key=cred_data.get("api_key"),
                merchant_id=cred_data.get("merchant_id"),
                webhook_secret=cred_data.get("webhook_secret"),
                base_url=settings.mylerz_base_url,
            )
    # Fallback to global settings
    return MylerzShippingService()
