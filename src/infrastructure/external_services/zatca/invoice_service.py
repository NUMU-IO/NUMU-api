"""ZATCA (Fatoora) e-invoicing service for Saudi Arabia.

Implements the ZATCA **Phase 1 (Generation)** TLV-encoded QR for simplified
tax invoices, plus the scaffolding/seams for **Phase 2 (Integration)**.
Mirrors the shape of the Egyptian ETA service so the invoice pipeline can
select by jurisdiction.

The QR is a Base64 string of concatenated TLV triplets
(``[tag][length][value]``). Phase 1 mandates 5 tags:

    1. Seller name
    2. VAT registration number (TRN)
    3. Invoice timestamp (ISO 8601)
    4. Invoice total (incl. VAT)
    5. VAT total

Phase 2 adds the cryptographic stamp — tags 6-9 (XML invoice hash, ECDSA
signature, public key, ZATCA stamp signature) — which requires CSID
onboarding and XAdES signing against the ZATCA sandbox/production APIs.
Those are intentionally left as explicit seams (``submit_invoice`` etc.)
because they need external onboarding + certificates, not just code.

Spec: ZATCA E-Invoicing (Fatoora) — Security Features Implementation
Standards. The TLV length field is a single byte, so each value must be
< 256 bytes (true for a name/TRN/timestamp/amount).
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


def _tlv(tag: int, value: str) -> bytes:
    """Encode one TLV triplet: tag (1 byte) + length (1 byte) + UTF-8 value."""
    encoded = value.encode("utf-8")
    if len(encoded) > 255:
        # ZATCA Phase 1 uses a single-byte length; truncate defensively
        # rather than emit a malformed QR. Real values never hit this.
        encoded = encoded[:255]
    return bytes([tag, len(encoded)]) + encoded


@dataclass(frozen=True)
class ZatcaInvoiceData:
    """The five Phase-1 fields encoded into the QR."""

    seller_name: str
    vat_number: str
    timestamp: str  # ISO 8601, e.g. "2026-06-02T12:34:56Z"
    total_with_vat: str  # decimal string, e.g. "115.00"
    vat_total: str  # decimal string, e.g. "15.00"


class ZATCAInvoiceService:
    """Saudi e-invoicing (Fatoora) service.

    Phase 1 (QR generation) is implemented and dependency-free. Phase 2
    (clearance/reporting) raises until CSID onboarding + XAdES signing are
    wired against the ZATCA APIs.
    """

    @property
    def country_code(self) -> str:
        return "SA"

    # ── Phase 1: QR generation ───────────────────────────────────────

    def generate_qr_tlv(self, data: ZatcaInvoiceData) -> str:
        """Build the Base64 TLV QR payload for a simplified tax invoice."""
        payload = (
            _tlv(1, data.seller_name)
            + _tlv(2, data.vat_number)
            + _tlv(3, data.timestamp)
            + _tlv(4, data.total_with_vat)
            + _tlv(5, data.vat_total)
        )
        return base64.b64encode(payload).decode("ascii")

    def generate_qr_for_invoice(
        self,
        *,
        seller_name: str,
        vat_number: str,
        timestamp: str,
        total_with_vat_cents: int,
        vat_total_cents: int,
        decimals: int = 2,
    ) -> str:
        """Convenience wrapper that formats cents → decimal strings then QR.

        Args:
            seller_name: Registered seller name.
            vat_number: 15-digit ZATCA TRN.
            timestamp: ISO 8601 invoice timestamp.
            total_with_vat_cents: Grand total (incl. VAT) in minor units.
            vat_total_cents: VAT amount in minor units.
            decimals: Currency decimal places (2 for SAR).
        """
        divisor = Decimal(10) ** decimals
        quant = Decimal("1").scaleb(-decimals)  # e.g. 0.01
        total_str = str((Decimal(total_with_vat_cents) / divisor).quantize(quant))
        vat_str = str((Decimal(vat_total_cents) / divisor).quantize(quant))
        return self.generate_qr_tlv(
            ZatcaInvoiceData(
                seller_name=seller_name,
                vat_number=vat_number,
                timestamp=timestamp,
                total_with_vat=total_str,
                vat_total=vat_str,
            )
        )

    # ── Phase 2: Integration (clearance / reporting) ─────────────────
    # These require a Cryptographic Stamp Identifier (CSID) obtained via
    # ZATCA onboarding, UBL 2.1 XML generation, and XAdES signing. They are
    # deliberate seams: the data model (zatca_* columns) and QR are ready,
    # but live submission needs sandbox/production certificates.

    async def submit_invoice(self, invoice) -> dict:  # pragma: no cover - seam
        """Clear (B2B) or report (B2C) an invoice with ZATCA.

        Not yet implemented — requires CSID onboarding + XAdES signing
        against the ZATCA APIs. Raising (rather than silently no-oping) so
        callers don't mistake an unsubmitted invoice for a cleared one.
        """
        raise NotImplementedError(
            "ZATCA Phase 2 (clearance/reporting) is not yet implemented. "
            "Phase 1 QR generation is available via generate_qr_for_invoice(). "
            "Phase 2 needs CSID onboarding and XAdES signing against the "
            "ZATCA sandbox/production APIs."
        )
