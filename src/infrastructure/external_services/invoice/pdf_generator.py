"""Invoice PDF generator using WeasyPrint.

Generates PDF invoices from Invoice entities with:
- Arabic (RTL) and English (LTR) text support via CSS direction
- ETA QR code embedding as base64 data URIs
- Egyptian Tax Authority compliance formatting
- Seller/buyer information layout with tax ID display
- Line items table with per-item tax breakdown

System requirements (installed in docker/Dockerfile):
    pango, cairo, gdk-pixbuf, harfbuzz, fontconfig

Font requirements (installed in docker/fonts/):
    Noto Sans Arabic (Regular + Bold) — OFL license from Google Fonts

Usage:
    from src.infrastructure.external_services.invoice import InvoicePDFGenerator

    generator = InvoicePDFGenerator()
    pdf_bytes = generator.generate(invoice)
    # Return as FastAPI response:
    # Response(content=pdf_bytes, media_type="application/pdf")

For high-volume generation, offload to Celery worker via asyncio.to_thread():
    pdf_bytes = await asyncio.run_in_executor(None, generator.generate, invoice)

References:
    - WeasyPrint docs: https://doc.courtbouillon.org/weasyprint/stable/
    - CSS Writing Modes (RTL): https://www.w3.org/TR/css-writing-modes-4/
    - Noto Sans Arabic: https://fonts.google.com/noto/specimen/Noto+Sans+Arabic
"""

import logging
from decimal import Decimal
from pathlib import Path

from src.core.entities.invoice import Invoice

logger = logging.getLogger(__name__)

# Template directory relative to this module
TEMPLATE_DIR = Path(__file__).parent / "templates"


class InvoicePDFGenerator:
    """Generates PDF invoices from Invoice entities via WeasyPrint.

    Renders a Jinja2 HTML template with invoice data and converts it to PDF
    using WeasyPrint's HTML-to-PDF engine. Supports Arabic RTL text via
    CSS ``direction: rtl`` and the Noto Sans Arabic font (installed via
    fontconfig in the Docker image).

    Attributes:
        template_name: Name of the Jinja2 template file in templates/.
        language: Language code ("ar" for Arabic/RTL, "en" for English/LTR).
    """

    def __init__(
        self,
        template_name: str = "invoice.html",
        language: str = "ar",
    ) -> None:
        self.template_name = template_name
        self.language = language
        self._template_path = TEMPLATE_DIR / template_name

    def generate(self, invoice: Invoice) -> bytes:
        """Generate PDF bytes from an Invoice entity.

        Args:
            invoice: The Invoice entity to render.

        Returns:
            PDF file contents as bytes.

        Raises:
            FileNotFoundError: If template file is missing.
            RuntimeError: If WeasyPrint is not installed or native libs are missing.
        """
        try:
            from weasyprint import HTML
        except ImportError as e:
            raise RuntimeError(
                "WeasyPrint is not installed. "
                "Install with: pip install 'weasyprint>=68.0' "
                "and ensure system libs (pango, cairo, etc.) are available."
            ) from e

        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Invoice template not found: {self._template_path}"
            )

        html_content = self._render_template(invoice)
        html = HTML(string=html_content, base_url=str(TEMPLATE_DIR))
        pdf_bytes: bytes = html.write_pdf()

        logger.info(
            "invoice_pdf_generated",
            extra={
                "invoice_number": invoice.invoice_number,
                "pdf_size_bytes": len(pdf_bytes),
                "language": self.language,
            },
        )

        return pdf_bytes

    def _render_template(self, invoice: Invoice) -> str:
        """Render the Jinja2 HTML template with invoice data."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        template = env.get_template(self.template_name)
        context = self._build_context(invoice)
        return template.render(**context)

    def _build_context(self, invoice: Invoice) -> dict:
        """Build the template rendering context from an Invoice entity.

        Formats monetary values from cents to display currency,
        prepares QR code data URI, and computes layout direction.
        """
        is_rtl = self.language == "ar"

        line_items = []
        for idx, item in enumerate(invoice.line_items, 1):
            tax_total = sum(
                (t.amount for t in item.taxes), Decimal("0")
            )
            line_items.append({
                "number": idx,
                "description": (
                    item.description_ar
                    if is_rtl and item.description_ar
                    else item.description
                ),
                "item_code": item.item_code,
                "quantity": f"{item.quantity:,.0f}",
                "unit_price": f"{item.unit_price:,.2f}",
                "discount": f"{item.discount:,.2f}",
                "net_total": f"{item.net_total:,.2f}",
                "tax_amount": f"{tax_total:,.2f}",
                "total": f"{item.total:,.2f}",
            })

        # QR code as data URI for embedding in <img> tag
        qr_data_uri = None
        if invoice.qr_code_image:
            qr_data_uri = f"data:image/png;base64,{invoice.qr_code_image}"

        return {
            "invoice": invoice,
            "line_items": line_items,
            "is_rtl": is_rtl,
            "direction": "rtl" if is_rtl else "ltr",
            "text_align": "right" if is_rtl else "left",
            "text_align_opposite": "left" if is_rtl else "right",
            "language": self.language,
            # Formatted totals (cents -> display currency)
            "subtotal": f"{invoice.subtotal / 100:,.2f}",
            "total_discount": f"{invoice.total_discount / 100:,.2f}",
            "total_taxes": f"{invoice.total_taxes / 100:,.2f}",
            "grand_total": f"{invoice.total / 100:,.2f}",
            "currency": invoice.currency,
            # QR code
            "qr_data_uri": qr_data_uri,
            # Party names (prefer Arabic when RTL)
            "seller_name": (
                invoice.seller.name_ar
                if is_rtl and invoice.seller.name_ar
                else invoice.seller.name
            ),
            "seller": invoice.seller,
            "buyer_name": (
                invoice.buyer.name_ar
                if is_rtl and invoice.buyer.name_ar
                else invoice.buyer.name
            ),
            "buyer": invoice.buyer,
            # Labels (Arabic / English)
            "labels": _LABELS_AR if is_rtl else _LABELS_EN,
        }


# Localized labels for the invoice template
_LABELS_AR = {
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

_LABELS_EN = {
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
