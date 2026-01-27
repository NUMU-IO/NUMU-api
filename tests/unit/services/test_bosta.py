"""Unit tests for Bosta shipping service."""

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.shipping_service import Parcel, ShippingAddress
from src.infrastructure.external_services.bosta.governorates import ShippingZone
from src.infrastructure.external_services.bosta.shipping_service import BostaShippingService


class TestBostaShippingService:
    """Tests for Bosta shipping service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = BostaShippingService(
            api_key="test_api_key",
            business_id="test_business_id",
            base_url="https://app.bosta.co/api/v2",
            webhook_secret="test_webhook_secret",
        )

        # Common test addresses
        self.cairo_address = ShippingAddress(
            name="Test Sender",
            street1="123 Test Street",
            city="Cairo",
            state="Cairo",
            country="Egypt",
            phone="+201234567890",
        )

        self.alex_address = ShippingAddress(
            name="Test Recipient",
            street1="456 Customer Ave",
            city="Alexandria",
            state="Alexandria",
            country="Egypt",
            phone="+201098765432",
        )

        self.test_parcel = Parcel(
            weight=2.0,
            length=30,
            width=20,
            height=10,
        )

    def test_get_zone_from_address_cairo(self):
        """Test zone detection for Cairo address."""
        zone = self.service._get_zone_from_address(self.cairo_address)
        assert zone == ShippingZone.GREATER_CAIRO

    def test_get_zone_from_address_alexandria(self):
        """Test zone detection for Alexandria address."""
        zone = self.service._get_zone_from_address(self.alex_address)
        assert zone == ShippingZone.DELTA

    def test_get_zone_from_address_unknown(self):
        """Test zone detection for unknown city defaults to Delta."""
        unknown_address = ShippingAddress(
            name="Test",
            street1="123 Street",
            city="Unknown City",
            country="Egypt",
        )
        zone = self.service._get_zone_from_address(unknown_address)
        assert zone == ShippingZone.DELTA

    def test_calculate_estimated_rate_greater_cairo(self):
        """Test rate calculation for Greater Cairo."""
        rate = self.service._calculate_estimated_rate(
            ShippingZone.GREATER_CAIRO,
            ShippingZone.GREATER_CAIRO,
            self.test_parcel,
        )
        assert rate == 4000  # 40 EGP base rate

    def test_calculate_estimated_rate_delta(self):
        """Test rate calculation for Delta zone."""
        rate = self.service._calculate_estimated_rate(
            ShippingZone.GREATER_CAIRO,
            ShippingZone.DELTA,
            self.test_parcel,
        )
        assert rate == 5000  # 50 EGP

    def test_calculate_estimated_rate_heavy_parcel(self):
        """Test rate calculation with weight surcharge."""
        heavy_parcel = Parcel(weight=8.0, length=30, width=20, height=10)  # 8kg
        rate = self.service._calculate_estimated_rate(
            ShippingZone.GREATER_CAIRO,
            ShippingZone.GREATER_CAIRO,
            heavy_parcel,
        )
        # Base 4000 + 3kg extra * 500 = 5500
        assert rate == 5500

    @pytest.mark.asyncio
    async def test_get_rates(self):
        """Test getting shipping rates."""
        # Without API, should return estimated rates
        service = BostaShippingService(
            api_key=None,  # No API key
            business_id=None,
        )

        rates = await service.get_rates(
            from_address=self.cairo_address,
            to_address=self.alex_address,
            parcel=self.test_parcel,
        )

        assert len(rates) >= 1
        standard_rate = rates[0]
        assert standard_rate.carrier == "Bosta"
        assert standard_rate.currency == "EGP"
        assert standard_rate.amount > 0

    @pytest.mark.asyncio
    async def test_get_rates_with_api(self):
        """Test getting rates from Bosta API."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "price": 55.00,
                "estimatedDays": 2,
            }

            # Create a mock client instance
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)

            # Make the context manager return the mock client
            mock_client_class.return_value.__aenter__.return_value = mock_client

            rates = await self.service.get_rates(
                from_address=self.cairo_address,
                to_address=self.alex_address,
                parcel=self.test_parcel,
            )

            assert len(rates) >= 1
            assert rates[0].amount == 5500  # 55 * 100 cents

    @pytest.mark.asyncio
    async def test_create_shipment(self):
        """Test creating a shipment."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.json.return_value = {
                "data": {
                    "_id": "delivery_123",
                    "trackingNumber": "BOSTA123456789",
                }
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            shipment = await self.service.create_shipment(
                from_address=self.cairo_address,
                to_address=self.alex_address,
                parcel=self.test_parcel,
                rate_id="bosta_standard_delta",
                cod_amount=10000,
                order_reference="order-123",
            )

            assert shipment.tracking_number == "BOSTA123456789"
            assert shipment.carrier == "Bosta"

    @pytest.mark.asyncio
    async def test_create_shipment_no_api_key(self):
        """Test creating shipment without API key fails."""
        service = BostaShippingService(api_key=None)

        with pytest.raises(ValueError) as exc_info:
            await service.create_shipment(
                from_address=self.cairo_address,
                to_address=self.alex_address,
                parcel=self.test_parcel,
                rate_id="bosta_standard",
            )

        assert "not configured" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_track_shipment(self):
        """Test tracking a shipment."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": {
                    "trackingNumber": "BOSTA123456789",
                    "state": {"value": "OUT_FOR_DELIVERY"},
                    "trackingLogs": [
                        {
                            "state": "PICKED_UP",
                            "description": "Package picked up",
                            "timestamp": "2024-01-02T09:00:00Z",
                        },
                        {
                            "state": "OUT_FOR_DELIVERY",
                            "description": "Out for delivery",
                            "timestamp": "2024-01-03T08:00:00Z",
                        },
                    ],
                    "expectedDeliveryDate": "2024-01-03T18:00:00Z",
                }
            }

            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            tracking = await self.service.track_shipment("Bosta", "BOSTA123456789")

            assert tracking.tracking_number == "BOSTA123456789"
            assert tracking.status == "out_for_delivery"
            assert len(tracking.events) == 2

    @pytest.mark.asyncio
    async def test_cancel_shipment(self):
        """Test cancelling a shipment."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200

            mock_client = MagicMock()
            mock_client.delete = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await self.service.cancel_shipment("BOSTA123456789")

            assert result is True

    @pytest.mark.asyncio
    async def test_validate_address_known_governorate(self):
        """Test validating address with known governorate."""
        is_valid, corrected = await self.service.validate_address(self.cairo_address)

        assert is_valid is True
        assert corrected is not None
        assert corrected.city == "Cairo"

    def test_verify_webhook_signature_valid(self):
        """Test verifying valid Bosta webhook signature."""
        payload = b'{"trackingNumber": "BOSTA123456789", "state": "DELIVERED"}'
        expected_sig = hmac.new(
            b"test_webhook_secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        result = self.service.verify_webhook_signature(payload, expected_sig)
        assert result is not None
        assert result["trackingNumber"] == "BOSTA123456789"

    def test_verify_webhook_signature_invalid(self):
        """Test verifying invalid Bosta webhook signature."""
        payload = b'{"trackingNumber": "BOSTA123456789"}'
        result = self.service.verify_webhook_signature(payload, "invalid_signature")
        assert result is None

    def test_verify_webhook_signature_no_secret(self):
        """Test webhook verification without secret."""
        service = BostaShippingService(
            api_key="test",
            webhook_secret=None,
        )
        result = service.verify_webhook_signature(b'{}', "sig")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_return(self):
        """Test requesting a return shipment."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": {
                    "trackingNumber": "BOSTA_RETURN_123",
                }
            }

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            return_tracking = await self.service.request_return(
                "BOSTA123456789",
                reason="Customer returned",
            )

            assert return_tracking == "BOSTA_RETURN_123"
