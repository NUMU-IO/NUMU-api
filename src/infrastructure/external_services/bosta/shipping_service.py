"""Bosta shipping service implementation for Egyptian market.

Bosta is Egypt's leading last-mile delivery company with coverage
across all 27 governorates. This service integrates with Bosta API
for shipment creation, tracking, and rate calculation.

API Documentation: https://developers.bosta.co/
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Any

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
from src.infrastructure.external_services.bosta.governorates import (
    ShippingZone,
    get_governorate_by_name,
)

logger = logging.getLogger(__name__)


# Base shipping rates by zone (in EGP cents)
# These are typical rates; actual rates come from Bosta API
ZONE_BASE_RATES = {
    ShippingZone.GREATER_CAIRO: 4000,  # 40 EGP
    ShippingZone.DELTA: 5000,  # 50 EGP
    ShippingZone.CANAL_SINAI: 5500,  # 55 EGP
    ShippingZone.UPPER_EGYPT: 6000,  # 60 EGP
    ShippingZone.REMOTE: 8000,  # 80 EGP
}

# Additional weight surcharge per kg over 5kg
WEIGHT_SURCHARGE_PER_KG = 500  # 5 EGP per kg


class BostaShippingService(IShippingService):
    """Bosta shipping service for Egyptian deliveries.

    Features:
    - Same-day and next-day delivery in Greater Cairo
    - COD (Cash on Delivery) support
    - Real-time tracking
    - Delivery confirmation with customer signature
    - Return shipment handling
    """

    def __init__(
        self,
        api_key: str | None = None,
        business_id: str | None = None,
        base_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.bosta_api_key
        self.business_id = business_id or settings.bosta_business_id
        self.base_url = base_url or settings.bosta_base_url
        self.webhook_secret = webhook_secret or settings.bosta_webhook_secret

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        if not self.api_key:
            raise ValueError("Bosta API key not configured")
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _calculate_estimated_rate(
        self,
        from_zone: ShippingZone,
        to_zone: ShippingZone,
        parcel: Parcel,
    ) -> int:
        """Calculate estimated shipping rate when API is unavailable.

        Args:
            from_zone: Origin shipping zone
            to_zone: Destination shipping zone
            parcel: Package details

        Returns:
            Estimated rate in cents (EGP)
        """
        # Use destination zone rate
        base_rate = ZONE_BASE_RATES.get(to_zone, ZONE_BASE_RATES[ShippingZone.DELTA])

        # Add weight surcharge for packages over 5kg
        if parcel.weight > 5:
            extra_kg = parcel.weight - 5
            base_rate += int(extra_kg * WEIGHT_SURCHARGE_PER_KG)

        return base_rate

    def _get_zone_from_address(self, address: ShippingAddress) -> ShippingZone:
        """Determine shipping zone from address.

        Args:
            address: Shipping address

        Returns:
            Shipping zone for the address
        """
        # Try to find governorate from city/state
        gov = get_governorate_by_name(address.city)
        if not gov and address.state:
            gov = get_governorate_by_name(address.state)

        if gov:
            return gov.zone

        # Default to Delta zone if unknown
        return ShippingZone.DELTA

    async def get_rates(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
    ) -> list[ShippingRate]:
        """Get shipping rates for a parcel.

        Args:
            from_address: Origin address
            to_address: Destination address
            parcel: Package dimensions and weight

        Returns:
            List of available shipping rates
        """
        # Determine zones
        from_zone = self._get_zone_from_address(from_address)
        to_zone = self._get_zone_from_address(to_address)

        rates = []

        # If API key is configured, try to get real rates
        if self.api_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/deliveries/pricing",
                        headers=self._get_headers(),
                        json={
                            "pickupAddress": {
                                "city": from_address.city,
                                "district": from_address.state or "",
                                "firstLine": from_address.street1,
                            },
                            "dropOffAddress": {
                                "city": to_address.city,
                                "district": to_address.state or "",
                                "firstLine": to_address.street1,
                            },
                            "type": 10,  # Delivery type
                            "specs": {
                                "weight": parcel.weight,
                                "size": f"{parcel.length}x{parcel.width}x{parcel.height}",
                            },
                        },
                        timeout=30.0,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        # Standard delivery
                        rates.append(
                            ShippingRate(
                                carrier="Bosta",
                                service="Standard Delivery",
                                rate_id=f"bosta_standard_{to_zone.value}",
                                amount=int(data.get("price", 0) * 100),  # Convert to cents
                                currency="EGP",
                                estimated_days=data.get("estimatedDays", 3),
                            )
                        )

            except Exception as e:
                logger.warning(f"Bosta API rate fetch failed: {e}, using estimates")

        # If no API rates, provide estimates
        if not rates:
            standard_rate = self._calculate_estimated_rate(from_zone, to_zone, parcel)

            # Standard delivery (2-3 days)
            rates.append(
                ShippingRate(
                    carrier="Bosta",
                    service="Standard Delivery",
                    rate_id=f"bosta_standard_{to_zone.value}",
                    amount=standard_rate,
                    currency="EGP",
                    estimated_days=3 if to_zone != ShippingZone.GREATER_CAIRO else 2,
                )
            )

            # Express delivery for Greater Cairo (same/next day)
            if to_zone == ShippingZone.GREATER_CAIRO:
                rates.append(
                    ShippingRate(
                        carrier="Bosta",
                        service="Express Delivery",
                        rate_id="bosta_express_cairo",
                        amount=int(standard_rate * 1.5),
                        currency="EGP",
                        estimated_days=1,
                    )
                )

        return rates

    async def create_shipment(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
        rate_id: str,
        cod_amount: int | None = None,
        order_reference: str | None = None,
        notes: str | None = None,
    ) -> ShipmentLabel:
        """Create a shipment and get AWB (Air Waybill).

        Args:
            from_address: Pickup address
            to_address: Delivery address
            parcel: Package details
            rate_id: Selected rate ID
            cod_amount: COD amount in cents (optional)
            order_reference: Your order reference
            notes: Delivery notes

        Returns:
            ShipmentLabel with tracking number and label URL
        """
        if not self.api_key:
            raise ValueError("Bosta API key not configured")

        # Determine delivery type
        delivery_type = 10  # Normal delivery
        if "express" in rate_id:
            delivery_type = 20  # Express delivery

        payload: dict[str, Any] = {
            "type": delivery_type,
            "specs": {
                "weight": parcel.weight,
                "packageDetails": {
                    "itemsCount": 1,
                    "description": notes or "Package",
                },
            },
            "dropOffAddress": {
                "city": to_address.city,
                "district": to_address.state or "",
                "firstLine": to_address.street1,
                "secondLine": to_address.street2 or "",
                "buildingNumber": "",
                "floor": "",
                "apartment": "",
            },
            "receiver": {
                "firstName": to_address.name.split()[0] if to_address.name else "Customer",
                "lastName": " ".join(to_address.name.split()[1:]) if to_address.name and len(to_address.name.split()) > 1 else "",
                "phone": to_address.phone or "",
            },
            "businessReference": order_reference or "",
            "notes": notes or "",
        }

        # Add COD if specified
        if cod_amount and cod_amount > 0:
            payload["cod"] = cod_amount / 100  # Convert cents to EGP

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/deliveries",
                headers=self._get_headers(),
                json=payload,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(f"Bosta shipment creation failed: {response.text}")
                raise ValueError(f"Failed to create Bosta shipment: {response.text}")

            data = response.json()
            delivery = data.get("data", data)

            tracking_number = delivery.get("trackingNumber", "")
            delivery_id = delivery.get("_id", "")

            return ShipmentLabel(
                label_url=f"https://app.bosta.co/delivery/{delivery_id}/awb",
                tracking_number=tracking_number,
                carrier="Bosta",
                service="Express" if delivery_type == 20 else "Standard",
            )

    async def track_shipment(
        self,
        carrier: str,
        tracking_number: str,
    ) -> TrackingInfo:
        """Track a Bosta shipment.

        Args:
            carrier: Carrier name (should be "Bosta")
            tracking_number: Bosta tracking number

        Returns:
            TrackingInfo with current status and events
        """
        if not self.api_key:
            raise ValueError("Bosta API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/deliveries/tracking/{tracking_number}",
                headers=self._get_headers(),
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.error(f"Bosta tracking failed: {response.text}")
                raise ValueError("Failed to track shipment")

            data = response.json()
            delivery = data.get("data", data)

            # Parse tracking events
            events = []
            for log in delivery.get("trackingLogs", []):
                events.append(
                    TrackingEvent(
                        status=log.get("state", ""),
                        description=log.get("description", ""),
                        location=log.get("location", None),
                        timestamp=datetime.fromisoformat(
                            log.get("timestamp", datetime.utcnow().isoformat()).replace("Z", "+00:00")
                        ),
                    )
                )

            # Map Bosta states to standard statuses
            bosta_state = delivery.get("state", {}).get("value", "")
            status_map = {
                "PENDING_PICKUP": "pending",
                "PICKED_UP": "in_transit",
                "IN_WAREHOUSE": "in_transit",
                "OUT_FOR_DELIVERY": "out_for_delivery",
                "DELIVERED": "delivered",
                "RETURNED": "returned",
                "CANCELLED": "cancelled",
            }
            status = status_map.get(bosta_state, "unknown")

            # Parse estimated delivery
            estimated_delivery = None
            if delivery.get("expectedDeliveryDate"):
                try:
                    estimated_delivery = datetime.fromisoformat(
                        delivery["expectedDeliveryDate"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            return TrackingInfo(
                carrier="Bosta",
                tracking_number=tracking_number,
                status=status,
                events=events,
                estimated_delivery=estimated_delivery,
            )

    async def validate_address(
        self,
        address: ShippingAddress,
    ) -> tuple[bool, ShippingAddress | None]:
        """Validate an Egyptian address.

        Args:
            address: Address to validate

        Returns:
            Tuple of (is_valid, corrected_address or None)
        """
        # Check if city matches a known governorate
        gov = get_governorate_by_name(address.city)
        if not gov and address.state:
            gov = get_governorate_by_name(address.state)

        if gov:
            # Valid governorate found
            # Could potentially correct city name
            corrected = ShippingAddress(
                name=address.name,
                street1=address.street1,
                street2=address.street2,
                city=gov.name_en,  # Normalize to English name
                state=gov.name_en,
                country="Egypt",
                zip=address.zip,
                phone=address.phone,
            )
            return (True, corrected)

        # If API key is configured, try API validation
        if self.api_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/cities/validate",
                        headers=self._get_headers(),
                        json={
                            "city": address.city,
                            "district": address.state or "",
                        },
                        timeout=30.0,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("isValid"):
                            return (True, address)

            except Exception as e:
                logger.warning(f"Bosta address validation failed: {e}")

        # Unknown address but not necessarily invalid
        return (True, address)

    async def cancel_shipment(self, tracking_number: str) -> bool:
        """Cancel a shipment before pickup.

        Args:
            tracking_number: Bosta tracking number

        Returns:
            True if successfully cancelled
        """
        if not self.api_key:
            raise ValueError("Bosta API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.base_url}/deliveries/{tracking_number}",
                headers=self._get_headers(),
                timeout=30.0,
            )

            if response.status_code in (200, 204):
                return True

            logger.warning(f"Bosta cancel failed: {response.text}")
            return False

    async def request_return(
        self,
        tracking_number: str,
        reason: str = "",
    ) -> str | None:
        """Request a return shipment.

        Args:
            tracking_number: Original delivery tracking number
            reason: Reason for return

        Returns:
            Return tracking number or None if failed
        """
        if not self.api_key:
            raise ValueError("Bosta API key not configured")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/deliveries/{tracking_number}/return",
                headers=self._get_headers(),
                json={"reason": reason},
                timeout=30.0,
            )

            if response.status_code in (200, 201):
                data = response.json()
                return data.get("data", {}).get("trackingNumber")

            logger.warning(f"Bosta return request failed: {response.text}")
            return None

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> dict | None:
        """Verify Bosta webhook signature.

        Args:
            payload: Webhook payload bytes
            signature: Signature header value

        Returns:
            Parsed payload if valid, None if invalid
        """
        if not self.webhook_secret:
            logger.warning("Bosta webhook secret not configured")
            return None

        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(expected_signature, signature):
                import json
                return json.loads(payload)
            else:
                logger.warning("Bosta webhook signature mismatch")
                return None

        except Exception as e:
            logger.error(f"Bosta webhook verification error: {e}")
            return None
