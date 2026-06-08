"""Unit tests for the ZATCA (Fatoora) e-invoicing service — Phase 1 QR."""

import base64

import pytest

from src.infrastructure.external_services.zatca.invoice_service import (
    ZatcaInvoiceData,
    ZATCAInvoiceService,
)


def _parse_tlv(blob: bytes) -> dict[int, str]:
    """Decode a ZATCA TLV byte string into {tag: value}."""
    out: dict[int, str] = {}
    i = 0
    while i < len(blob):
        tag = blob[i]
        length = blob[i + 1]
        value = blob[i + 2 : i + 2 + length].decode("utf-8")
        out[tag] = value
        i += 2 + length
    return out


class TestZATCAInvoiceService:
    def setup_method(self):
        self.service = ZATCAInvoiceService()

    def test_country_code(self):
        assert self.service.country_code == "SA"

    def test_generate_qr_tlv_roundtrips_all_five_tags(self):
        data = ZatcaInvoiceData(
            seller_name="Acme KSA",
            vat_number="300000000000003",
            timestamp="2026-06-02T12:34:56Z",
            total_with_vat="115.00",
            vat_total="15.00",
        )
        qr = self.service.generate_qr_tlv(data)

        # Valid Base64 that decodes to the expected TLV structure.
        decoded = base64.b64decode(qr)
        tlv = _parse_tlv(decoded)
        assert tlv[1] == "Acme KSA"
        assert tlv[2] == "300000000000003"
        assert tlv[3] == "2026-06-02T12:34:56Z"
        assert tlv[4] == "115.00"
        assert tlv[5] == "15.00"

    def test_generate_qr_handles_arabic_seller_name(self):
        data = ZatcaInvoiceData(
            seller_name="متجر تجريبي",
            vat_number="311111111111113",
            timestamp="2026-06-02T00:00:00Z",
            total_with_vat="230.00",
            vat_total="30.00",
        )
        qr = self.service.generate_qr_tlv(data)
        tlv = _parse_tlv(base64.b64decode(qr))
        assert tlv[1] == "متجر تجريبي"
        # length byte must reflect UTF-8 byte length, not character count
        assert tlv[2] == "311111111111113"

    def test_generate_qr_for_invoice_formats_cents(self):
        qr = self.service.generate_qr_for_invoice(
            seller_name="Acme KSA",
            vat_number="300000000000003",
            timestamp="2026-06-02T12:34:56Z",
            total_with_vat_cents=11500,
            vat_total_cents=1500,
            decimals=2,
        )
        tlv = _parse_tlv(base64.b64decode(qr))
        assert tlv[4] == "115.00"
        assert tlv[5] == "15.00"

    @pytest.mark.asyncio
    async def test_submit_invoice_is_a_phase2_seam(self):
        with pytest.raises(NotImplementedError):
            await self.service.submit_invoice(object())
