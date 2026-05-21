#!/usr/bin/env python3
"""Verify WeasyPrint PDF generation with Arabic RTL support.

Generates a sample invoice PDF containing Arabic text and saves it to
scripts/sample_invoice.pdf for manual visual inspection.

Usage:
    python scripts/verify_pdf_generation.py

Requirements:
    - WeasyPrint >= 68.0 and its system dependencies (pango, cairo, etc.)
    - Noto Sans Arabic font installed (via fontconfig or docker/fonts/)

This script is NOT part of the automated test suite — it produces a
visual artifact for manual verification that Arabic RTL rendering,
layout, and QR code embedding work correctly.
"""

import sys
from decimal import Decimal
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceType,
    SellerInfo,
)
from src.infrastructure.external_services.invoice.pdf_generator import (
    InvoicePDFGenerator,
)


def create_sample_invoice() -> Invoice:
    """Create a sample invoice with Arabic text for testing."""
    seller = SellerInfo(
        tax_id="100-200-300",
        name="NUMU Technologies Ltd.",
        name_ar="نومو للتكنولوجيا",
        branch_id="0",
        country="EG",
        governorate="القاهرة",
        city="مدينة نصر",
        street="شارع عباس العقاد",
        building_number="15",
    )

    buyer = BuyerInfo(
        buyer_type="B",
        tax_id="400-500-600",
        name="Acme Trading Co.",
        name_ar="شركة أكمي للتجارة",
        country="EG",
        governorate="الجيزة",
        city="الدقي",
        street="شارع التحرير",
        building_number="42",
        phone="+201234567890",
        email="billing@acme-trading.eg",
    )

    invoice = Invoice(
        store_id="00000000-0000-0000-0000-000000000001",
        invoice_number="INV-2026-0001",
        internal_id="NUMU-001",
        invoice_type=InvoiceType.INVOICE,
        seller=seller,
        buyer=buyer,
        currency="EGP",
        notes="Thank you for your business.",
        notes_ar="شكراً لتعاملكم معنا. نتطلع للعمل معكم مرة أخرى.",
    )

    # Add line items with Arabic descriptions
    invoice.add_line_item(
        description="Premium Cotton T-Shirt (Large)",
        description_ar="تي شيرت قطن ممتاز (كبير)",
        item_code="EG-TS-001",
        quantity=Decimal("10"),
        unit_price=Decimal("250.00"),
        discount=Decimal("100.00"),
    )

    invoice.add_line_item(
        description="Handmade Leather Wallet",
        description_ar="محفظة جلد طبيعي يدوية الصنع",
        item_code="EG-WL-002",
        quantity=Decimal("5"),
        unit_price=Decimal("450.00"),
        discount=Decimal("0"),
    )

    invoice.add_line_item(
        description="Egyptian Cotton Scarf",
        description_ar="وشاح قطن مصري",
        item_code="EG-SC-003",
        quantity=Decimal("20"),
        unit_price=Decimal("120.00"),
        discount=Decimal("50.00"),
    )

    return invoice


def main() -> None:
    """Generate sample PDF and report results."""
    print("=" * 60)
    print("NUMU Invoice PDF Generation Verification")
    print("=" * 60)

    invoice = create_sample_invoice()

    # Generate Arabic RTL version
    print("\n[1/2] Generating Arabic (RTL) invoice...")
    try:
        generator_ar = InvoicePDFGenerator(language="ar")
        pdf_ar = generator_ar.generate(invoice)
        output_ar = Path(__file__).parent / "sample_invoice_ar.pdf"
        output_ar.write_bytes(pdf_ar)
        print(f"  OK  Arabic PDF: {output_ar} ({len(pdf_ar):,} bytes)")
    except Exception as e:
        print(f"  FAIL  Arabic PDF generation failed: {e}")
        sys.exit(1)

    # Generate English LTR version
    print("\n[2/2] Generating English (LTR) invoice...")
    try:
        generator_en = InvoicePDFGenerator(language="en")
        pdf_en = generator_en.generate(invoice)
        output_en = Path(__file__).parent / "sample_invoice_en.pdf"
        output_en.write_bytes(pdf_en)
        print(f"  OK  English PDF: {output_en} ({len(pdf_en):,} bytes)")
    except Exception as e:
        print(f"  FAIL  English PDF generation failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("VERIFICATION CHECKLIST (manual):")
    print("-" * 60)
    print("Open the generated PDFs and verify:")
    print("  [ ] Arabic text renders correctly (proper ligatures)")
    print("  [ ] RTL layout: text flows right-to-left")
    print("  [ ] Table columns align properly")
    print("  [ ] Monetary values are formatted with commas")
    print("  [ ] Seller and buyer info displays correctly")
    print("  [ ] Page size is A4")
    print("  [ ] Font is Noto Sans Arabic (not boxes/tofu)")
    print("=" * 60)


if __name__ == "__main__":
    main()
