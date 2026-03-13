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

    # Default fallback logo (NUMU branding) shipped with the template
    _DEFAULT_LOGO = TEMPLATE_DIR / "numu_logo.png"

    def __init__(
        self,
        template_name: str = "invoice.html",
        language: str = "ar",
        store_logo_url: str | None = None,
    ) -> None:
        self.template_name = template_name
        self.language = language
        self.store_logo_url = store_logo_url
        self._template_path = TEMPLATE_DIR / template_name

    def generate(self, invoice: Invoice) -> bytes:
        """Generate PDF bytes from an Invoice entity.

        Tries WeasyPrint first (full HTML/CSS rendering with RTL support).
        Falls back to fpdf2 (pure Python, no native deps) when WeasyPrint
        is unavailable (e.g. on Windows without Cairo/Pango).

        Args:
            invoice: The Invoice entity to render.

        Returns:
            PDF file contents as bytes.
        """
        try:
            from weasyprint import HTML

            if not self._template_path.exists():
                raise FileNotFoundError(
                    f"Invoice template not found: {self._template_path}"
                )

            html_content = self._render_template(invoice)
            html = HTML(string=html_content, base_url=str(TEMPLATE_DIR))
            pdf_bytes: bytes = html.write_pdf()
        except (ImportError, OSError):
            logger.info("weasyprint_unavailable_using_fpdf2")
            pdf_bytes = self._generate_fpdf2(invoice)

        logger.info(
            "invoice_pdf_generated",
            extra={
                "invoice_number": invoice.invoice_number,
                "pdf_size_bytes": len(pdf_bytes),
                "language": self.language,
            },
        )

        return pdf_bytes

    def _generate_fpdf2(self, invoice: Invoice) -> bytes:
        """Generate bilingual invoice PDF using fpdf2 (pure Python fallback).

        Mirrors the bilingual HTML template (Arabic primary + English secondary).
        Uses arabic-reshaper + python-bidi for proper Arabic rendering.
        """
        import arabic_reshaper
        from bidi.algorithm import get_display
        from fpdf import FPDF

        def ar(text: str) -> str:
            """Reshape and reorder Arabic text for correct PDF rendering."""
            if not text:
                return text
            reshaped = arabic_reshaper.reshape(text)
            return get_display(reshaped)

        # Colours
        BLUE = (16, 52, 166)  # #1034A6
        DARK = (26, 26, 46)  # #1a1a2e
        GREY = (108, 117, 125)  # #6c757d
        LIGHT_GREY = (173, 181, 189)  # #adb5bd
        BG_LIGHT = (241, 243, 245)  # #f1f3f5

        # Font paths
        font_dir = (
            Path(__file__).parent.parent.parent.parent.parent / "docker" / "fonts"
        )
        ar_regular = font_dir / "NotoSansArabic-Regular.ttf"
        ar_bold = font_dir / "NotoSansArabic-Bold.ttf"

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Page dimensions
        pw = pdf.w - pdf.l_margin - pdf.r_margin  # printable width

        # Register Arabic fonts if available
        has_arabic_font = ar_regular.exists()
        if has_arabic_font:
            pdf.add_font("NotoArabic", "", str(ar_regular), uni=True)
            pdf.add_font("NotoArabic", "B", str(ar_bold), uni=True)

        def set_font(style="", size=10):
            if has_arabic_font:
                pdf.set_font("NotoArabic", style, size)
            else:
                pdf.set_font("Helvetica", style, size)

        # ── Header ──────────────────────────────────────────────────
        # Title (bilingual, centered)
        set_font("B", 20)
        pdf.set_text_color(*BLUE)
        pdf.cell(0, 10, ar("فاتورة ضريبية"), new_x="LMARGIN", new_y="NEXT", align="C")
        set_font("B", 12)
        pdf.cell(0, 6, "Tax Invoice", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_text_color(*DARK)

        # Blue divider line
        y = pdf.get_y() + 2
        pdf.set_draw_color(*BLUE)
        pdf.set_line_width(0.8)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.set_y(y + 4)

        # Invoice meta info (bilingual)
        date_str = invoice.date_issued.strftime("%Y-%m-%d %H:%M")
        meta_items = [
            (ar("رقم الفاتورة"), "Invoice No.", invoice.invoice_number),
            (ar("التاريخ"), "Date", date_str),
            (ar("العملة"), "Currency", invoice.currency),
        ]
        if invoice.internal_id:
            meta_items.append((ar("رقم الطلب"), "Order", invoice.internal_id))

        set_font("B", 9)
        for label_ar, label_en, value in meta_items:
            pdf.set_text_color(*DARK)
            pdf.cell(30, 5, label_ar, new_x="RIGHT")
            set_font("", 8)
            pdf.set_text_color(*GREY)
            pdf.cell(20, 5, f"/ {label_en}", new_x="RIGHT")
            set_font("B", 9)
            pdf.set_text_color(*DARK)
            pdf.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(6)

        # ── Seller & Buyer boxes ────────────────────────────────────
        seller = invoice.seller
        buyer = invoice.buyer

        box_w = (pw - 6) / 2  # 6mm gap between boxes
        box_x_left = pdf.l_margin
        box_x_right = pdf.l_margin + box_w + 6
        box_y_start = pdf.get_y()

        for side, party, box_x, label_ar, label_en in [
            ("seller", seller, box_x_left, ar("البائع"), "SELLER"),
            ("buyer", buyer, box_x_right, ar("المشتري"), "BUYER"),
        ]:
            pdf.set_xy(box_x, box_y_start)

            # Box background
            pdf.set_fill_color(250, 251, 252)
            pdf.set_draw_color(222, 226, 230)
            pdf.rect(box_x, box_y_start, box_w, 36, style="DF")

            # Box label
            cx = box_x + 3
            cy = box_y_start + 2
            pdf.set_xy(cx, cy)
            set_font("", 7)
            pdf.set_text_color(*GREY)
            pdf.cell(
                box_w - 6, 4, f"{label_ar} / {label_en}", new_x="LMARGIN", new_y="NEXT"
            )

            # Divider inside box
            pdf.set_draw_color(233, 236, 239)
            pdf.line(cx, cy + 5, box_x + box_w - 3, cy + 5)

            # Name (Arabic bold, English small grey)
            cy += 7
            pdf.set_xy(cx, cy)
            set_font("B", 11)
            pdf.set_text_color(*DARK)
            name_ar = ar(party.name_ar) if party.name_ar else party.name
            pdf.cell(box_w - 6, 5, name_ar, new_x="LMARGIN", new_y="NEXT")
            cy += 5
            pdf.set_xy(cx, cy)
            set_font("", 8)
            pdf.set_text_color(*GREY)
            pdf.cell(box_w - 6, 4, party.name, new_x="LMARGIN", new_y="NEXT")
            cy += 5

            # Tax ID
            if party.tax_id:
                pdf.set_xy(cx, cy)
                set_font("", 8)
                pdf.set_text_color(*GREY)
                pdf.cell(
                    box_w - 6,
                    4,
                    f"{ar('الرقم الضريبي')} / Tax ID: {party.tax_id}",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                cy += 5

            # Address
            addr_parts = []
            if party.building_number:
                addr_parts.append(party.building_number)
            if party.street:
                addr_parts.append(party.street)
            if party.city:
                addr_parts.append(party.city)
            if hasattr(party, "governorate") and party.governorate:
                addr_parts.append(party.governorate)
            if addr_parts:
                pdf.set_xy(cx, cy)
                pdf.set_text_color(73, 80, 87)
                pdf.cell(
                    box_w - 6,
                    4,
                    ar(", ".join(addr_parts)),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                cy += 5

            # Phone / email (buyer only)
            if side == "buyer":
                if party.phone:
                    pdf.set_xy(cx, cy)
                    pdf.cell(box_w - 6, 4, party.phone, new_x="LMARGIN", new_y="NEXT")
                    cy += 5
                if party.email:
                    pdf.set_xy(cx, cy)
                    pdf.cell(box_w - 6, 4, party.email, new_x="LMARGIN", new_y="NEXT")

        pdf.set_y(box_y_start + 38)

        # ── Line items table ────────────────────────────────────────
        col_w = [10, 55, 20, 25, 25, 25, 30]
        headers_ar = [
            "#",
            ar("الوصف"),
            ar("الكمية"),
            ar("سعر الوحدة"),
            ar("الخصم"),
            ar("الضريبة"),
            ar("الإجمالي"),
        ]
        headers_en = [
            "#",
            "Description",
            "Qty",
            "Unit Price",
            "Discount",
            "Tax",
            "Total",
        ]

        # Table header row (bilingual)
        pdf.set_fill_color(*BG_LIGHT)
        set_font("B", 8)
        pdf.set_text_color(73, 80, 87)
        for i, (h_ar, h_en) in enumerate(zip(headers_ar, headers_en)):
            x = pdf.get_x()
            y_h = pdf.get_y()
            pdf.set_fill_color(*BG_LIGHT)
            pdf.rect(x, y_h, col_w[i], 10, style="F")
            # Arabic label
            set_font("B", 8)
            pdf.set_text_color(73, 80, 87)
            pdf.set_xy(x, y_h + 0.5)
            pdf.cell(col_w[i], 4, h_ar, align="C")
            # English label
            set_font("", 6)
            pdf.set_text_color(134, 142, 150)
            pdf.set_xy(x, y_h + 5)
            pdf.cell(col_w[i], 4, h_en, align="C")
            # Move to next column
            pdf.set_xy(x + col_w[i], y_h)

        # Blue line under header
        y_after_header = pdf.get_y() + 10
        pdf.set_draw_color(*BLUE)
        pdf.set_line_width(0.5)
        pdf.line(pdf.l_margin, y_after_header, pdf.w - pdf.r_margin, y_after_header)
        pdf.set_y(y_after_header + 1)

        # Table rows
        set_font("", 9)
        pdf.set_text_color(*DARK)
        for idx, item in enumerate(invoice.line_items, 1):
            tax_total = sum((t.amount for t in item.taxes), Decimal("0"))

            # Alternate row background
            if idx % 2 == 0:
                pdf.set_fill_color(250, 251, 252)
                pdf.rect(pdf.l_margin, pdf.get_y(), pw, 7, style="F")

            set_font("", 9)
            pdf.set_text_color(*DARK)
            pdf.cell(col_w[0], 7, str(idx), border=0, align="C")

            # Description: Arabic + English
            desc_ar = item.description_ar or item.description
            if len(desc_ar) > 28:
                desc_ar = desc_ar[:25] + "..."
            pdf.cell(col_w[1], 7, ar(desc_ar), border=0, align="R")

            pdf.cell(col_w[2], 7, f"{item.quantity:,.0f}", border=0, align="C")
            pdf.cell(col_w[3], 7, f"{item.unit_price:,.2f}", border=0, align="R")
            pdf.cell(col_w[4], 7, f"{item.discount:,.2f}", border=0, align="R")
            pdf.cell(col_w[5], 7, f"{tax_total:,.2f}", border=0, align="R")
            pdf.cell(col_w[6], 7, f"{item.total:,.2f}", border=0, align="R")
            pdf.ln()

            # Light separator line
            pdf.set_draw_color(238, 238, 238)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())

        pdf.ln(6)

        # ── Totals ──────────────────────────────────────────────────
        totals_x = 115

        def _total_row(label_ar: str, label_en: str, value: str, bold: bool = False):
            pdf.set_x(totals_x)
            style = "B" if bold else ""
            size = 11 if bold else 9
            set_font(style, size)
            pdf.set_text_color(*GREY)
            pdf.cell(30, 7, f"{ar(label_ar)}", align="R")
            set_font("", 7)
            pdf.set_text_color(*LIGHT_GREY)
            pdf.cell(15, 7, f"/ {label_en}", align="R")
            set_font(style, size)
            if bold:
                pdf.set_text_color(*BLUE)
            else:
                pdf.set_text_color(*DARK)
            pdf.cell(30, 7, value, new_x="LMARGIN", new_y="NEXT", align="R")

        _total_row(
            "المجموع الفرعي",
            "Subtotal",
            f"{invoice.subtotal / 100:,.2f} {invoice.currency}",
        )

        if invoice.total_discount:
            _total_row(
                "إجمالي الخصم",
                "Discount",
                f"-{invoice.total_discount / 100:,.2f} {invoice.currency}",
            )

        if invoice.total_taxes:
            _total_row(
                "إجمالي الضريبة",
                "Tax (14%)",
                f"{invoice.total_taxes / 100:,.2f} {invoice.currency}",
            )

        # Grand total divider
        pdf.set_draw_color(*BLUE)
        pdf.set_line_width(0.5)
        pdf.line(totals_x, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1)

        _total_row(
            "الإجمالي الكلي",
            "Grand Total",
            f"{invoice.total / 100:,.2f} {invoice.currency}",
            bold=True,
        )

        # ── QR Code ─────────────────────────────────────────────────
        if invoice.qr_code_image:
            import base64
            import tempfile

            try:
                qr_bytes = base64.b64decode(invoice.qr_code_image)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(qr_bytes)
                    tmp_path = tmp.name

                pdf.ln(8)
                # Separator
                pdf.set_draw_color(233, 236, 239)
                pdf.set_line_width(0.3)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
                pdf.ln(4)

                # Center the QR code
                qr_size = 28
                x_center = (pdf.w - qr_size) / 2
                pdf.image(tmp_path, x=x_center, w=qr_size, h=qr_size)
                pdf.ln(qr_size + 2)

                import os

                os.unlink(tmp_path)
            except Exception:
                logger.warning("fpdf2_qr_code_render_failed")

        # ── ETA Footer ──────────────────────────────────────────────
        pdf.ln(6)
        # Separator
        pdf.set_draw_color(233, 236, 239)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(4)

        set_font("", 8)
        pdf.set_text_color(*GREY)
        pdf.cell(
            0,
            4,
            ar("هذه فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرائب المصرية"),
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        set_font("", 7)
        pdf.set_text_color(*LIGHT_GREY)
        pdf.cell(
            0,
            4,
            "This is an electronic invoice issued per Egyptian Tax Authority requirements",
            align="C",
        )

        return bytes(pdf.output())

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

    def _resolve_logo_url(self) -> str | None:
        """Resolve the logo URL for the template.

        Uses store_logo_url if provided, otherwise falls back to the
        default NUMU logo bundled in the templates directory.
        """
        if self.store_logo_url:
            return self.store_logo_url
        if self._DEFAULT_LOGO.exists():
            return self._DEFAULT_LOGO.as_uri()
        return None

    def _build_context(self, invoice: Invoice) -> dict:
        """Build the template rendering context from an Invoice entity.

        Formats monetary values from cents to display currency,
        prepares QR code data URI, and computes layout direction.
        """
        is_rtl = self.language in ("ar", "ar_en")
        is_bilingual = self.language == "ar_en"

        line_items = []
        for idx, item in enumerate(invoice.line_items, 1):
            tax_total = sum((t.amount for t in item.taxes), Decimal("0"))
            line_items.append({
                "number": idx,
                "description": (
                    item.description_ar
                    if is_rtl and item.description_ar
                    else item.description
                ),
                "description_ar": item.description_ar,
                "description_en": item.description,
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
            "is_bilingual": is_bilingual,
            "direction": "rtl" if is_rtl else "ltr",
            "text_align": "right" if is_rtl else "left",
            "text_align_opposite": "left" if is_rtl else "right",
            "language": self.language,
            # Logo
            "logo_url": self._resolve_logo_url(),
            # Formatted totals (cents -> display currency)
            "subtotal": f"{invoice.subtotal / 100:,.2f}",
            "total_discount": f"{invoice.total_discount / 100:,.2f}",
            "total_taxes": f"{invoice.total_taxes / 100:,.2f}",
            "grand_total": f"{invoice.total / 100:,.2f}",
            "currency": invoice.currency,
            # QR code
            "qr_data_uri": qr_data_uri,
            # Party info
            "seller": invoice.seller,
            "buyer": invoice.buyer,
            # Bilingual labels (always pass both for the bilingual template)
            "labels_ar": _LABELS_AR,
            "labels_en": _LABELS_EN,
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
