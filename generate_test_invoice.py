"""Generate a test invoice using the NUMU invoice template.

Creates a realistic Egyptian e-invoice with sample seller/buyer data
and multiple line items. Outputs:
  - test_invoice.html  (open in any browser)
  - test_invoice.pdf   (if WeasyPrint is installed)

Usage:
    python generate_test_invoice.py
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

# ---------------------------------------------------------------------------
# Minimal inline data structures (avoids importing the full project)
# ---------------------------------------------------------------------------


class InvoiceType(StrEnum):
    INVOICE = "I"
    CREDIT_NOTE = "C"
    DEBIT_NOTE = "D"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"


class _Obj:
    """Simple attribute container to mimic Pydantic models in the template."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Arabic + English labels (copied from pdf_generator.py)
# ---------------------------------------------------------------------------
LABELS_AR_EN = {
    "invoice": "فاتورة ضريبية",
    "invoice_number": "رقم الفاتورة",
    "date": "التاريخ",
    "seller": "البائع",
    "buyer": "المشتري",
    "tax_id": "الرقم الضريبي",
    "address": "العنوان",
    "item_no": "#",
    "description": "الوصف",
    "code": "الكود",
    "qty": "الكمية",
    "unit_price": "سعر الوحدة",
    "discount": "الخصم",
    "net": "الصافي",
    "tax": "الضريبة",
    "total": "الإجمالي",
    "subtotal": "المجموع الفرعي",
    "total_discount": "إجمالي الخصم",
    "total_tax": "إجمالي الضريبة",
    "grand_total": "الإجمالي الكلي",
    "currency": "العملة",
    "notes": "ملاحظات",
    "eta_notice": "هذه فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرائب المصرية",
    "page": "صفحة",
}

LABELS_EN = {
    "invoice": "Tax Invoice",
    "invoice_number": "Invoice No.",
    "date": "Date",
    "seller": "Seller",
    "buyer": "Buyer",
    "tax_id": "Tax ID",
    "address": "Address",
    "item_no": "#",
    "description": "Description",
    "code": "Code",
    "qty": "Qty",
    "unit_price": "Unit Price",
    "discount": "Discount",
    "net": "Net",
    "tax": "Tax",
    "total": "Total",
    "subtotal": "Subtotal",
    "total_discount": "Total Discount",
    "total_tax": "Total Tax",
    "grand_total": "Grand Total",
    "currency": "Currency",
    "notes": "Notes",
    "eta_notice": "This is an electronic invoice issued per Egyptian Tax Authority requirements",
    "page": "Page",
}


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def build_test_invoice():
    """Build a realistic test invoice with Egyptian data."""

    seller = _Obj(
        tax_id="514-786-4321",
        name="NUMU Technologies Ltd.",
        name_ar="شركة نومو للتكنولوجيا",
        branch_id="0",
        country="EG",
        governorate="Cairo",
        city="Nasr City",
        street="Abbas El-Akkad St.",
        building_number="45",
        activity_code="6201",
    )

    buyer = _Obj(
        buyer_type="B",
        tax_id="200-431-8765",
        national_id=None,
        name="Al-Nile Trading Co.",
        name_ar="شركة النيل للتجارة",
        country="EG",
        governorate="Giza",
        city="Dokki",
        street="Tahrir St.",
        building_number="12",
        phone="+20 100 123 4567",
        email="accounts@nile-trading.eg",
    )

    now = datetime.now(UTC)

    invoice = _Obj(
        id=uuid4(),
        invoice_number="INV-2026-000042",
        internal_id="NUMU-42",
        invoice_type=InvoiceType.INVOICE,
        status=InvoiceStatus.DRAFT,
        date_issued=now,
        seller=seller,
        buyer=buyer,
        currency="EGP",
        notes="Payment due within 30 days. Bank transfer preferred.",
        notes_ar="الدفع خلال 30 يوم. التحويل البنكي مفضل.",
        qr_code_image=None,
        eta_uuid=None,
        eta_long_id=None,
    )

    # --- Line items with 14% VAT ---
    raw_items = [
        {
            "description": "Annual SaaS Subscription - Pro Plan",
            "description_ar": "اشتراك سنوي - الخطة الاحترافية",
            "item_code": "EG-SVC-001",
            "quantity": Decimal("1"),
            "unit_price": Decimal("12000.00"),
            "discount": Decimal("1000.00"),
        },
        {
            "description": "Custom Domain Setup",
            "description_ar": "إعداد نطاق مخصص",
            "item_code": "EG-SVC-002",
            "quantity": Decimal("2"),
            "unit_price": Decimal("500.00"),
            "discount": Decimal("0"),
        },
        {
            "description": "WhatsApp Business Integration",
            "description_ar": "تكامل واتساب للأعمال",
            "item_code": "EG-SVC-003",
            "quantity": Decimal("1"),
            "unit_price": Decimal("3500.00"),
            "discount": Decimal("350.00"),
        },
        {
            "description": "Payment Gateway Setup (Paymob)",
            "description_ar": "إعداد بوابة الدفع (باي موب)",
            "item_code": "EG-SVC-004",
            "quantity": Decimal("1"),
            "unit_price": Decimal("2500.00"),
            "discount": Decimal("0"),
        },
        {
            "description": "SMS Notification Credits (5000 msgs)",
            "description_ar": "رصيد إشعارات SMS (5000 رسالة)",
            "item_code": "EG-SVC-005",
            "quantity": Decimal("5000"),
            "unit_price": Decimal("0.15"),
            "discount": Decimal("50.00"),
        },
    ]

    vat_rate = Decimal("14.00")
    line_items = []
    subtotal_cents = 0
    total_discount_cents = 0
    total_taxes_cents = 0

    for idx, item in enumerate(raw_items, 1):
        qty = item["quantity"]
        price = item["unit_price"]
        disc = item["discount"]

        sales_total = qty * price
        net_total = sales_total - disc
        tax_amount = net_total * vat_rate / Decimal("100")
        total = net_total + tax_amount

        line_items.append({
            "number": idx,
            "description": item["description"],
            "description_ar": item["description_ar"],
            "description_en": item["description"],
            "item_code": item["item_code"],
            "quantity": f"{qty:,.0f}",
            "unit_price": f"{price:,.2f}",
            "discount": f"{disc:,.2f}",
            "net_total": f"{net_total:,.2f}",
            "tax_amount": f"{tax_amount:,.2f}",
            "total": f"{total:,.2f}",
        })

        subtotal_cents += int(net_total * 100)
        total_discount_cents += int(disc * 100)
        total_taxes_cents += int(tax_amount * 100)

    grand_total_cents = subtotal_cents + total_taxes_cents

    return {
        "invoice": invoice,
        "line_items": line_items,
        "seller": seller,
        "buyer": buyer,
        "seller_name": seller.name_ar,
        "buyer_name": buyer.name_ar,
        "is_rtl": True,
        "is_bilingual": True,
        "direction": "rtl",
        "text_align": "right",
        "text_align_opposite": "left",
        "language": "ar",
        "logo_url": None,
        "subtotal": f"{subtotal_cents / 100:,.2f}",
        "total_discount": f"{total_discount_cents / 100:,.2f}",
        "total_taxes": f"{total_taxes_cents / 100:,.2f}",
        "grand_total": f"{grand_total_cents / 100:,.2f}",
        "currency": "EGP",
        "qr_data_uri": None,
        "labels": LABELS_AR_EN,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def main():
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError:
        print("ERROR: jinja2 is required.  pip install jinja2")
        sys.exit(1)

    template_dir = (
        Path(__file__).parent
        / "src"
        / "infrastructure"
        / "external_services"
        / "invoice"
        / "templates"
    )
    if not template_dir.exists():
        print(f"ERROR: Template directory not found: {template_dir}")
        sys.exit(1)

    # Build test data
    context = build_test_invoice()

    # --- Render bilingual Arabic/English template ---
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("invoice_ar.html")
    html_ar = template.render(**context)

    out_html_ar = Path(__file__).parent / "test_invoice_ar.html"
    out_html_ar.write_text(html_ar, encoding="utf-8")
    print(f"Bilingual invoice (AR/EN): {out_html_ar}")

    # --- Render English-only template ---
    context_en = {**context}
    context_en["labels"] = LABELS_EN
    context_en["is_rtl"] = False
    context_en["direction"] = "ltr"
    context_en["text_align"] = "left"
    context_en["text_align_opposite"] = "right"
    context_en["language"] = "en"
    context_en["seller_name"] = context["seller"].name
    context_en["buyer_name"] = context["buyer"].name

    template_en = env.get_template("invoice.html")
    html_en = template_en.render(**context_en)

    out_html_en = Path(__file__).parent / "test_invoice_en.html"
    out_html_en.write_text(html_en, encoding="utf-8")
    print(f"English invoice:           {out_html_en}")

    # --- Attempt PDF generation via WeasyPrint ---
    try:
        from weasyprint import HTML

        pdf_ar = HTML(string=html_ar, base_url=str(template_dir)).write_pdf()
        out_pdf_ar = Path(__file__).parent / "test_invoice_ar.pdf"
        out_pdf_ar.write_bytes(pdf_ar)
        print(f"PDF (AR/EN):               {out_pdf_ar}  ({len(pdf_ar):,} bytes)")

        pdf_en = HTML(string=html_en, base_url=str(template_dir)).write_pdf()
        out_pdf_en = Path(__file__).parent / "test_invoice_en.pdf"
        out_pdf_en.write_bytes(pdf_en)
        print(f"PDF (EN):                  {out_pdf_en}  ({len(pdf_en):,} bytes)")

    except ImportError:
        print("\nWeasyPrint not installed - PDF generation skipped.")
        print("Install with: pip install weasyprint")
        print("(Also requires system libs: pango, cairo, gdk-pixbuf, harfbuzz)")
    except Exception as e:
        print(f"\nPDF generation failed: {e}")
        print("HTML files were generated successfully - open them in a browser.")

    print("\nTest invoice data:")
    print(f"  Invoice #:     {context['invoice'].invoice_number}")
    print(f"  Seller:        {context['seller'].name}")
    print(f"  Buyer:         {context['buyer'].name}")
    print(f"  Items:         {len(context['line_items'])}")
    print(f"  Subtotal:      {context['subtotal']} EGP")
    print(f"  Discount:      {context['total_discount']} EGP")
    print(f"  Tax (14%):     {context['total_taxes']} EGP")
    print(f"  Grand Total:   {context['grand_total']} EGP")


if __name__ == "__main__":
    main()
