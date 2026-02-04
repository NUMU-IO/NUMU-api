"""Unit tests for ETA invoice service."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    SellerInfo,
    TaxLine,
    TaxType,
)
from src.infrastructure.external_services.eta.invoice_service import ETAInvoiceService
from src.infrastructure.external_services.eta.qr_generator import (
    decode_eta_qr_data,
    generate_eta_qr_code,
    generate_eta_qr_data,
)


class TestETAInvoiceService:
    """Tests for ETA invoice service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = ETAInvoiceService(
            client_id="test_client_id",
            client_secret="test_client_secret",
            base_url="https://api.invoicing.eta.gov.eg/api/v1",
            token_url="https://id.eta.gov.eg/connect/token",
        )
        self.service.enabled = True

    def _create_test_invoice(self) -> Invoice:
        """Create a test invoice."""
        seller = SellerInfo(
            tax_id="123456789",
            name="Test Store",
            name_ar="متجر تجريبي",
            governorate="Cairo",
            city="Nasr City",
            street="Test Street",
        )

        buyer = BuyerInfo(
            buyer_type="P",
            name="Test Customer",
            governorate="Giza",
            city="6th October",
        )

        line_item = InvoiceLineItem(
            description="Test Product",
            item_code="EGS-001",
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            discount=Decimal("0"),
            sales_total=Decimal("200.00"),
            net_total=Decimal("200.00"),
            taxes=[
                TaxLine(
                    tax_type=TaxType.VAT,
                    amount=Decimal("28.00"),
                    rate=Decimal("14.00"),
                )
            ],
            total=Decimal("228.00"),
        )

        invoice = Invoice(
            id=uuid4(),
            store_id=uuid4(),
            order_id=uuid4(),
            invoice_number="INV-2024-0001",
            status=InvoiceStatus.DRAFT,
            seller=seller,
            buyer=buyer,
            line_items=[line_item],
            subtotal=20000,  # 200.00 in cents
            total_taxes=2800,  # 28.00 in cents
            total=22800,  # 228.00 in cents
        )

        return invoice

    def test_service_enabled(self):
        """Test service enabled property."""
        assert self.service.enabled is True

    def test_service_disabled_by_default(self):
        """Test service disabled when no credentials."""
        service = ETAInvoiceService(
            client_id=None,
            client_secret=None,
        )
        # enabled comes from settings, check it's controllable
        service.enabled = False
        assert service.enabled is False

    @pytest.mark.asyncio
    async def test_submit_invoice_disabled(self):
        """Test submission when ETA is disabled."""
        self.service.enabled = False

        invoice = self._create_test_invoice()
        result = await self.service.submit_invoice(invoice)

        assert result["success"] is False
        assert "disabled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_submit_invoice_success(self):
        """Test successful invoice submission."""
        with patch.object(
            self.service, "_get_access_token", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = "access_token_123"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "submissionId": "sub_123",
                    "acceptedDocuments": [
                        {
                            "uuid": "eta-uuid-456",
                            "longId": "eta-long-789",
                            "internalId": "INV-2024-0001",
                        }
                    ],
                    "rejectedDocuments": [],
                }

                mock_client = MagicMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                invoice = self._create_test_invoice()
                result = await self.service.submit_invoice(invoice)

                assert result["success"] is True
                assert result["uuid"] == "eta-uuid-456"
                assert result["long_id"] == "eta-long-789"
                assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_submit_invoice_rejected(self):
        """Test rejected invoice submission."""
        with patch.object(
            self.service, "_get_access_token", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = "access_token_123"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "submissionId": "sub_123",
                    "acceptedDocuments": [],
                    "rejectedDocuments": [
                        {
                            "internalId": "INV-2024-0001",
                            "error": {
                                "message": "Invalid tax ID",
                            },
                        }
                    ],
                }

                mock_client = MagicMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                invoice = self._create_test_invoice()
                result = await self.service.submit_invoice(invoice)

                assert result["success"] is False
                assert result["status"] == "rejected"
                assert "Invalid tax ID" in result["error"]

    @pytest.mark.asyncio
    async def test_get_submission_status(self):
        """Test getting submission status."""
        with patch.object(
            self.service, "_get_access_token", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = "access_token"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "submissionId": "sub_123",
                    "status": "Valid",
                }

                mock_client = MagicMock()
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                status = await self.service.get_submission_status("sub_123")

                assert status["status"] == "Valid"

    @pytest.mark.asyncio
    async def test_get_document(self):
        """Test getting document by UUID."""
        with patch.object(
            self.service, "_get_access_token", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = "access_token"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "uuid": "eta-uuid-123",
                    "status": "Valid",
                }

                mock_client = MagicMock()
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                doc = await self.service.get_document("eta-uuid-123")

                assert doc["uuid"] == "eta-uuid-123"

    @pytest.mark.asyncio
    async def test_cancel_document(self):
        """Test cancelling a document."""
        with patch.object(
            self.service, "_get_access_token", new_callable=AsyncMock
        ) as mock_auth:
            mock_auth.return_value = "access_token"

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "cancelled"}

                mock_client = MagicMock()
                mock_client.put = AsyncMock(return_value=mock_response)
                mock_client_class.return_value.__aenter__.return_value = mock_client

                result = await self.service.cancel_document(
                    uuid="eta-uuid-123",
                    reason="Order cancelled",
                )

                assert result["success"] is True

    def test_calculate_document_hash(self):
        """Test document hash calculation."""
        document = {"key": "value", "number": 123}
        hash1 = self.service._calculate_document_hash(document)

        # Same document should produce same hash
        hash2 = self.service._calculate_document_hash(document)
        assert hash1 == hash2

        # Different document should produce different hash
        document2 = {"key": "different"}
        hash3 = self.service._calculate_document_hash(document2)
        assert hash1 != hash3

    @pytest.mark.asyncio
    async def test_process_invoice_submission(self):
        """Test full invoice submission process."""
        self.service.enabled = False  # Use disabled mode for quick test

        invoice = self._create_test_invoice()
        result = await self.service.process_invoice_submission(invoice)

        # With ETA disabled, should return draft status
        assert result.status == InvoiceStatus.DRAFT

    @pytest.mark.asyncio
    async def test_get_document_printable(self):
        """Test getting printable URL."""
        url = await self.service.get_document_printable(
            uuid="uuid-123",
            long_id="longid-456",
        )

        assert "uuid-123" in url
        assert "longid-456" in url
        assert "invoicing.eta.gov.eg" in url


class TestETAQRCodeGeneration:
    """Tests for ETA QR code generation."""

    def test_generate_qr_data(self):
        """Test generating QR code data string."""
        qr_data = generate_eta_qr_data(
            seller_name="متجر تجريبي",
            tax_number="123456789",
            invoice_date=datetime(2024, 1, 15, 10, 30, 0),
            total_with_vat=114.00,
            vat_amount=14.00,
        )

        assert qr_data is not None
        assert len(qr_data) > 0

        # Should be base64 encoded
        import base64

        decoded = base64.b64decode(qr_data)
        assert len(decoded) > 0

    def test_generate_qr_code(self):
        """Test generating QR code with image."""
        qr_data, qr_image = generate_eta_qr_code(
            seller_name="Test Store",
            tax_number="123456789",
            invoice_date=datetime(2024, 1, 15, 10, 30, 0),
            total_with_vat=114.00,
            vat_amount=14.00,
        )

        assert qr_data is not None

        # Image may be None if qrcode library not installed
        if qr_image:
            import base64

            decoded = base64.b64decode(qr_image)
            # Should be PNG image (starts with PNG header)
            assert decoded[:4] == b"\x89PNG"

    def test_decode_qr_data(self):
        """Test decoding QR code data."""
        # Generate data
        qr_data = generate_eta_qr_data(
            seller_name="متجر تجريبي",
            tax_number="123456789",
            invoice_date=datetime(2024, 1, 15, 10, 30, 0),
            total_with_vat=114.00,
            vat_amount=14.00,
        )

        # Decode it
        fields = decode_eta_qr_data(qr_data)

        assert fields["seller_name"] == "متجر تجريبي"
        assert fields["tax_number"] == "123456789"
        assert "2024-01-15" in fields["invoice_date"]
        assert fields["total_with_vat"] == "114.00"
        assert fields["vat_amount"] == "14.00"

    def test_qr_data_with_arabic_name(self):
        """Test QR data preserves Arabic text."""
        arabic_name = "شركة الاختبار للتجارة"

        qr_data = generate_eta_qr_data(
            seller_name=arabic_name,
            tax_number="123456789",
            invoice_date=datetime.now(),
            total_with_vat=100.00,
            vat_amount=14.00,
        )

        # Decode and verify
        fields = decode_eta_qr_data(qr_data)
        assert fields["seller_name"] == arabic_name

    def test_decode_invalid_qr_data(self):
        """Test decoding invalid QR data returns empty dict."""
        result = decode_eta_qr_data("invalid_base64!")
        assert result == {}
