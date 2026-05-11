"""Invoice PDF generator — NUMU brand kit v1.0.

Generates PDF invoices from Invoice entities with:
- Arabic (RTL) primary, English (LTR) secondary text via CSS direction
- ETA QR code embedded as base64 data URI
- Egyptian Tax Authority compliance formatting
- Seller/buyer info with tax ID display
- Line items table with per-item tax breakdown
- Optional payment-status stamp (paid/pending/refunded/etc.) read from the
  linked order — caller passes ``payment={status, method, paid_at}`` kwarg
- NUMU brand logo (SVG via WeasyPrint, PNG via fpdf2 fallback)

Two render paths share the same brand colors (loaded from
``brand/tokens.json``):
- WeasyPrint (Linux/Docker production): full HTML/CSS rendering, SVG logo.
- fpdf2 (Windows local dev / no Cairo): pure Python; PNG logo via
  ``pdf.image()``; matches the HTML layout's brand-color choices.

System requirements (installed in docker/Dockerfile):
    pango, cairo, gdk-pixbuf, harfbuzz, fontconfig

Font requirements (installed in docker/fonts/):
    Noto Sans Arabic (Regular + Bold) — OFL license from Google Fonts.

Usage:
    from src.infrastructure.external_services.invoice import InvoicePDFGenerator

    gen = InvoicePDFGenerator(template_name="invoice_ar.html", language="ar_en")
    pdf_bytes = gen.generate(invoice, payment={"status": "paid", "method": "COD", "paid_at": "2026-05-04"})
"""

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.core.entities.invoice import Invoice

logger = logging.getLogger(__name__)

# Template directory relative to this module
TEMPLATE_DIR = Path(__file__).parent / "templates"

# Workspace root → brand/tokens.json (6 levels up from this file).
# Read at import time so both render paths share one palette source.
_BRAND_TOKENS_PATH = (
    Path(__file__).parent.parent.parent.parent.parent.parent / "brand" / "tokens.json"
)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert "#RRGGBB" → (r, g, b) ints in 0..255."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _load_brand_tokens() -> dict[str, Any]:
    """Read brand/tokens.json. Falls back to a hardcoded NUMU Navy palette
    if the file is missing so unit tests don't require workspace layout."""
    try:
        return json.loads(_BRAND_TOKENS_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover — never fatal
        logger.warning("brand_tokens_load_failed_using_defaults")
        return {
            "color": {
                "semantic": {
                    "primary": "#003366",
                    "background": "#FBF6ED",
                    "surface_alt": "#F5EFE6",
                    "border": "#EAE0CE",
                    "muted": "#8AA5C2",
                    "ink": "#0F1624",
                    "ink_soft": "#2B3344",
                    "success": "#6B8E68",
                    "warning": "#E8A430",
                    "danger": "#C14A1C",
                    "info": "#1F4A7A",
                },
            }
        }


_BRAND = _load_brand_tokens()


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

    # Default fallback logo (NUMU branding) shipped with the template.
    # SVG is vector — WeasyPrint renders it natively at any size; the
    # PNG variant remains as a raster fallback for older renderers.
    _DEFAULT_LOGO = TEMPLATE_DIR / "numu_logo.svg"
    _DEFAULT_LOGO_PNG = TEMPLATE_DIR / "numu_logo.png"

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

    def generate(
        self,
        invoice: Invoice,
        payment: dict[str, Any] | None = None,
    ) -> bytes:
        """Generate PDF bytes from an Invoice entity.

        Tries WeasyPrint first (full HTML/CSS rendering with RTL support).
        Falls back to fpdf2 (pure Python, no native deps) when WeasyPrint
        is unavailable (e.g. on Windows without Cairo/Pango). Both paths
        consume the same payment context.

        Args:
            invoice: The Invoice entity to render.
            payment: Optional payment context with keys:
                - ``status`` (str): one of paid / pending / authorized /
                  partially_refunded / refunded / failed.
                - ``method`` (str | None): human-readable label
                  (e.g. "Cash on Delivery", "Paymob").
                - ``paid_at`` (str | None): pre-formatted timestamp.
                When provided, a colored payment-status stamp renders below
                the totals.

        Returns:
            PDF file contents as bytes.
        """
        try:
            from weasyprint import HTML

            if not self._template_path.exists():
                raise FileNotFoundError(
                    f"Invoice template not found: {self._template_path}"
                )

            html_content = self._render_template(invoice, payment=payment)
            html = HTML(string=html_content, base_url=str(TEMPLATE_DIR))
            pdf_bytes: bytes = html.write_pdf()
        except (ImportError, OSError):
            logger.info("weasyprint_unavailable_using_fpdf2")
            pdf_bytes = self._generate_fpdf2(invoice, payment=payment)

        logger.info(
            "invoice_pdf_generated",
            extra={
                "invoice_number": invoice.invoice_number,
                "pdf_size_bytes": len(pdf_bytes),
                "language": self.language,
                "payment_status": (payment or {}).get("status"),
            },
        )

        return pdf_bytes

    def _generate_fpdf2(
        self,
        invoice: Invoice,
        payment: dict[str, Any] | None = None,
    ) -> bytes:
        """Generate bilingual invoice PDF using fpdf2 (pure Python fallback).

        Mirrors the bilingual HTML template (Arabic primary + English).
        Uses arabic-reshaper + python-bidi for Arabic shaping. Brand colors
        derived from brand/tokens.json so this output matches WeasyPrint.
        Embeds the NUMU PNG logo (templates/numu_logo.png) in the header.
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

        # Brand palette (derived from brand/tokens.json so the rasterized
        # fpdf2 output stays aligned with the WeasyPrint HTML/CSS output).
        _s = _BRAND["color"]["semantic"]
        NAVY = _hex_to_rgb(_s["primary"])  # #003366 — NUMU Navy
        INK = _hex_to_rgb(_s["ink"])  # #0F1624 — Ink
        INK_SOFT = _hex_to_rgb(_s["ink_soft"])  # #2B3344
        MUTED = _hex_to_rgb(_s["muted"])  # #8AA5C2 — Navy 300
        SURFACE = _hex_to_rgb(_s["surface_alt"])  # #F5EFE6 — Cream
        BORDER = _hex_to_rgb(_s["border"])  # #EAE0CE — Bone
        SUCCESS = _hex_to_rgb(_s["success"])  # #6B8E68 — Sage
        WARNING = _hex_to_rgb(_s["warning"])  # #E8A430 — Saffron
        DANGER = _hex_to_rgb(_s["danger"])  # #C14A1C — Terracotta
        INFO = _hex_to_rgb(_s["info"])  # #1F4A7A — Navy 600
        # Aliases kept for code that previously used semantic names.
        DARK = INK
        GREY = INK_SOFT
        BG_LIGHT = SURFACE

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

        # Register Arabic fonts if available. NotoSansArabic only ships
        # Arabic glyphs — Latin codepoints would render as missing-glyph
        # boxes if we used it for English text. We pick the right font
        # per-cell via ``set_font_for(text)``.
        import re as _re

        _ARABIC_RE = _re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

        has_arabic_font = ar_regular.exists()
        if has_arabic_font:
            pdf.add_font("NotoArabic", "", str(ar_regular), uni=True)
            pdf.add_font("NotoArabic", "B", str(ar_bold), uni=True)

        def has_arabic(s: str) -> bool:
            return bool(s and _ARABIC_RE.search(s))

        def set_font(style="", size=10):
            """Default font setter — Arabic when the font is available,
            else Helvetica. Used where we know the content language up-front
            (e.g. headlines, totals labels)."""
            if has_arabic_font:
                pdf.set_font("NotoArabic", style, size)
            else:
                pdf.set_font("Helvetica", style, size)

        def set_font_for(text: str, style="", size=10):
            """Pick font based on whether ``text`` contains Arabic glyphs.
            Latin-only strings render with Helvetica (which has Latin
            glyphs); anything containing Arabic uses NotoArabic. Falls
            back to Helvetica when the Arabic font is absent."""
            if has_arabic_font and has_arabic(text):
                pdf.set_font("NotoArabic", style, size)
            else:
                pdf.set_font("Helvetica", style, size)

        def cell_smart(w, h, text, **kwargs):
            """``pdf.cell`` wrapper that picks the right font per text.
            For mixed Arabic+Latin strings (e.g. ``"البائع / SELLER"``)
            we still pick NotoArabic so the Arabic part renders — Latin
            chars in a label like ``" / SELLER"`` will be missing-glyph.
            Use ``cell_split(...)`` instead when the mix matters."""
            current_style = pdf.font_style or ""
            current_size = pdf.font_size_pt
            set_font_for(text, current_style, current_size)
            pdf.cell(w, h, text, **kwargs)

        def cell_split_mixed(w, h, ar_text, en_text, sep=" / ", **kwargs):
            """Render an Arabic + Latin label across the cell's width by
            picking the right font for each half. Used for box labels and
            similar bilingual headers where both halves must render."""
            current_style = pdf.font_style or ""
            current_size = pdf.font_size_pt
            x0, y0 = pdf.get_x(), pdf.get_y()

            # Arabic part
            set_font_for(ar_text, current_style, current_size)
            ar_w = pdf.get_string_width(ar_text)
            pdf.cell(ar_w, h, ar_text)

            # Separator + Latin part with Helvetica
            pdf.set_font("Helvetica", current_style, current_size)
            tail = f"{sep}{en_text}"
            tail_w = pdf.get_string_width(tail)
            pdf.cell(tail_w, h, tail)

            # Move pen to the cell's nominal end
            pdf.set_xy(x0 + w, y0)
            if kwargs.get("new_x") == "LMARGIN":
                pdf.set_x(pdf.l_margin)
            if kwargs.get("new_y") == "NEXT":
                pdf.set_y(y0 + h)

        # ── Header: NUMU logo (left) + bilingual title (centered) ────
        # WeasyPrint uses the SVG; fpdf2 needs a raster — embed the PNG
        # bundled at templates/numu_logo.png. Logo file may be absent in
        # some unit-test environments — silently skip in that case.
        logo_png = self._DEFAULT_LOGO_PNG
        header_top_y = pdf.get_y()
        logo_height = 16  # mm
        if logo_png.exists():
            try:
                pdf.image(str(logo_png), x=pdf.l_margin, y=header_top_y, h=logo_height)
            except Exception:  # pragma: no cover — never fatal
                logger.warning("fpdf2_logo_embed_failed", extra={"path": str(logo_png)})

        # Title block — pulled down so it visually aligns with the logo
        pdf.set_y(header_top_y + 2)
        set_font("B", 22)  # Arabic font for the Arabic title
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 9, ar("فاتورة ضريبية"), new_x="LMARGIN", new_y="NEXT", align="C")
        # Subtitle in Latin — Helvetica so the glyphs actually exist
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 5, "TAX INVOICE", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_text_color(*INK)

        # Ensure we cleared the logo before drawing the divider
        post_header_y = max(pdf.get_y() + 2, header_top_y + logo_height + 4)

        # Navy divider line
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.8)
        pdf.line(pdf.l_margin, post_header_y, pdf.w - pdf.r_margin, post_header_y)
        pdf.set_y(post_header_y + 4)

        # Invoice meta info (bilingual)
        date_str = invoice.date_issued.strftime("%Y-%m-%d %H:%M")
        meta_items = [
            (ar("رقم الفاتورة"), "Invoice No.", invoice.invoice_number),
            (ar("التاريخ"), "Date", date_str),
            (ar("العملة"), "Currency", invoice.currency),
        ]
        if invoice.internal_id:
            meta_items.append((ar("رقم الطلب"), "Order", invoice.internal_id))

        for label_ar, label_en, value in meta_items:
            # Arabic label — NotoArabic
            set_font("B", 9)
            pdf.set_text_color(*DARK)
            pdf.cell(30, 5, label_ar, new_x="RIGHT")
            # Latin "/ Invoice No." — Helvetica so the slash + Latin glyphs render
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*GREY)
            pdf.cell(20, 5, f"/ {label_en}", new_x="RIGHT")
            # Value (e.g. "INV-2026-000002") — Latin, Helvetica
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*DARK)
            pdf.cell(0, 5, str(value), new_x="LMARGIN", new_y="NEXT", align="R")
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

            # Box background — cream surface, bone border (brand kit)
            pdf.set_fill_color(*SURFACE)
            pdf.set_draw_color(*BORDER)
            pdf.rect(box_x, box_y_start, box_w, 38, style="DF")

            # Box label — Arabic part with NotoArabic, " / SELLER" with Helvetica
            cx = box_x + 3
            cy = box_y_start + 2
            pdf.set_xy(cx, cy)
            set_font("B", 7)
            pdf.set_text_color(*NAVY)
            ar_label_w = pdf.get_string_width(label_ar)
            pdf.cell(ar_label_w, 4, label_ar)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(
                box_w - 6 - ar_label_w,
                4,
                f" / {label_en}",
                new_x="LMARGIN",
                new_y="NEXT",
            )

            # Divider inside box
            pdf.set_draw_color(*BORDER)
            pdf.line(cx, cy + 5, box_x + box_w - 3, cy + 5)

            # Primary name line. If name_ar holds Arabic, render with the
            # Arabic font; otherwise treat both name fields as Latin.
            cy += 7
            pdf.set_xy(cx, cy)
            pdf.set_text_color(*INK)
            primary_name = party.name_ar or party.name or ""
            if has_arabic(primary_name):
                set_font("B", 11)
                pdf.cell(box_w - 6, 5, ar(primary_name), new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(box_w - 6, 5, primary_name, new_x="LMARGIN", new_y="NEXT")
            cy += 5

            # Secondary line: the OTHER of {name, name_ar} when they differ.
            secondary = (
                party.name if party.name and party.name != primary_name else None
            )
            if secondary:
                pdf.set_xy(cx, cy)
                pdf.set_text_color(*INK_SOFT)
                if has_arabic(secondary):
                    set_font("", 8)
                    pdf.cell(box_w - 6, 4, ar(secondary), new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.set_font("Helvetica", "", 8)
                    pdf.cell(box_w - 6, 4, secondary, new_x="LMARGIN", new_y="NEXT")
                cy += 5

            # Tax ID — Arabic label + Latin number, split fonts so both render
            if party.tax_id:
                pdf.set_xy(cx, cy)
                pdf.set_text_color(*INK_SOFT)
                set_font("", 8)
                ar_lbl = ar("الرقم الضريبي")
                w_ar = pdf.get_string_width(ar_lbl)
                pdf.cell(w_ar, 4, ar_lbl)
                pdf.set_font("Helvetica", "", 8)
                pdf.cell(
                    box_w - 6 - w_ar,
                    4,
                    f" / Tax ID: {party.tax_id}",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                cy += 5

            # Address — pick font based on whether the joined string has Arabic
            addr_parts = []
            if party.building_number:
                addr_parts.append(str(party.building_number))
            if party.street:
                addr_parts.append(str(party.street))
            if party.city:
                addr_parts.append(str(party.city))
            if hasattr(party, "governorate") and party.governorate:
                addr_parts.append(str(party.governorate))
            if addr_parts:
                addr = ", ".join(addr_parts)
                pdf.set_xy(cx, cy)
                pdf.set_text_color(*INK_SOFT)
                if has_arabic(addr):
                    set_font("", 8)
                    pdf.cell(box_w - 6, 4, ar(addr), new_x="LMARGIN", new_y="NEXT")
                else:
                    pdf.set_font("Helvetica", "", 8)
                    pdf.cell(box_w - 6, 4, addr, new_x="LMARGIN", new_y="NEXT")
                cy += 5

            # Phone / email (buyer only) — both are pure Latin
            if side == "buyer":
                if party.phone:
                    pdf.set_xy(cx, cy)
                    pdf.set_font("Helvetica", "", 8)
                    pdf.set_text_color(*INK_SOFT)
                    pdf.cell(
                        box_w - 6, 4, str(party.phone), new_x="LMARGIN", new_y="NEXT"
                    )
                    cy += 5
                if party.email:
                    pdf.set_xy(cx, cy)
                    pdf.set_font("Helvetica", "", 8)
                    pdf.set_text_color(*INK_SOFT)
                    pdf.cell(
                        box_w - 6, 4, str(party.email), new_x="LMARGIN", new_y="NEXT"
                    )

        pdf.set_y(box_y_start + 40)

        # ── Line items table ────────────────────────────────────────
        # Tax-free invoice — no Tax column. Width totals 180mm = printable
        # A4 width minus margins. Description column gets the freed space
        # so longer product names fit comfortably.
        col_w = [10, 80, 20, 30, 20, 20]
        headers_ar = [
            "#",
            ar("الوصف"),
            ar("الكمية"),
            ar("سعر الوحدة"),
            ar("الخصم"),
            ar("الإجمالي"),
        ]
        headers_en = [
            "#",
            "Description",
            "Qty",
            "Unit Price",
            "Discount",
            "Total",
        ]

        # Table header row (bilingual)
        pdf.set_fill_color(*BG_LIGHT)
        set_font("B", 8)
        pdf.set_text_color(73, 80, 87)
        for i, (h_ar, h_en) in enumerate(zip(headers_ar, headers_en)):
            x = pdf.get_x()
            y_h = pdf.get_y()
            # Navy header bar (matches WeasyPrint template)
            pdf.set_fill_color(*NAVY)
            pdf.rect(x, y_h, col_w[i], 10, style="F")
            # Arabic label — NotoArabic, on navy
            set_font("B", 8)
            pdf.set_text_color(*SURFACE)
            pdf.set_xy(x, y_h + 0.5)
            pdf.cell(col_w[i], 4, h_ar, align="C")
            # English label — Helvetica so e.g. "Description" actually renders
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(*MUTED)
            pdf.set_xy(x, y_h + 5)
            pdf.cell(col_w[i], 4, h_en, align="C")
            # Move to next column
            pdf.set_xy(x + col_w[i], y_h)

        # Spacer below header — no need for the extra blue rule, the navy
        # bar carries the visual weight
        y_after_header = pdf.get_y() + 10
        pdf.set_y(y_after_header + 1)

        # Table rows
        for idx, item in enumerate(invoice.line_items, 1):
            # Alternate row background — cream/surface
            if idx % 2 == 0:
                pdf.set_fill_color(*SURFACE)
                pdf.rect(pdf.l_margin, pdf.get_y(), pw, 7, style="F")

            pdf.set_text_color(*INK)

            # # column — Latin digit
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(col_w[0], 7, str(idx), border=0, align="C")

            # Description column — pick font based on language. Truncate to
            # avoid spilling into the next column. The wider description
            # column (80mm vs the old 55mm) lets us bump the cap to 42 chars.
            desc = item.description_ar or item.description or ""
            if len(desc) > 42:
                desc = desc[:39] + "..."
            if has_arabic(desc):
                set_font("", 9)
                pdf.cell(col_w[1], 7, ar(desc), border=0, align="R")
            else:
                pdf.set_font("Helvetica", "", 9)
                pdf.cell(col_w[1], 7, desc, border=0, align="L")

            # Numeric columns — Helvetica. Total = net_total (tax-free)
            # so the figures match the no-tax totals block below.
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(col_w[2], 7, f"{item.quantity:,.0f}", border=0, align="C")
            pdf.cell(col_w[3], 7, f"{item.unit_price:,.2f}", border=0, align="R")
            pdf.cell(col_w[4], 7, f"{item.discount:,.2f}", border=0, align="R")
            pdf.cell(col_w[5], 7, f"{item.net_total:,.2f}", border=0, align="R")
            pdf.ln()

            # Light separator line
            pdf.set_draw_color(*BORDER)
            pdf.set_line_width(0.2)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())

        pdf.ln(6)

        # ── Totals ──────────────────────────────────────────────────
        totals_x = 115

        def _total_row(label_ar: str, label_en: str, value: str, bold: bool = False):
            pdf.set_x(totals_x)
            style = "B" if bold else ""
            size = 11 if bold else 9
            # Arabic label — NotoArabic
            set_font(style, size)
            pdf.set_text_color(*INK_SOFT)
            pdf.cell(30, 7, ar(label_ar), align="R")
            # Latin slug — Helvetica
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*MUTED)
            pdf.cell(15, 7, f"/ {label_en}", align="R")
            set_font(style, size)
            # Value (e.g. "38.00 EGP") — Latin, Helvetica
            pdf.set_font("Helvetica", style, size)
            if bold:
                pdf.set_text_color(*NAVY)
            else:
                pdf.set_text_color(*INK)
            pdf.cell(30, 7, value, new_x="LMARGIN", new_y="NEXT", align="R")

        # Tax-free totals: skip the Tax row and base both Subtotal and
        # Grand Total on the pre-tax subtotal so the math is internally
        # consistent. The underlying entity still carries total_taxes for
        # ETA submission elsewhere — we just don't render it.
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

        # Grand total divider
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.5)
        pdf.line(totals_x, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1)

        grand_total_cents = invoice.subtotal - (invoice.total_discount or 0)
        _total_row(
            "الإجمالي الكلي",
            "Grand Total",
            f"{grand_total_cents / 100:,.2f} {invoice.currency}",
            bold=True,
        )

        # ── Payment status stamp (optional) ─────────────────────────
        if payment and payment.get("status"):
            raw_status = payment.get("status")
            status_key = str(raw_status).split(".")[-1].lower()

            stamp_color_map = {
                "paid": SUCCESS,
                "pending": WARNING,
                "unpaid": WARNING,
                "authorized": INFO,
                "partially_refunded": DANGER,
                "refunded": DANGER,
                "failed": DANGER,
            }
            stamp_rgb = stamp_color_map.get(status_key, GREY)
            label_ar = _LABELS_AR["payment"].get(status_key, status_key)
            label_en = _LABELS_EN["payment"].get(status_key, status_key)

            # Render the stamp by composing two cells: Arabic part with
            # NotoArabic, " / Latin" with Helvetica. Width is computed from
            # both pieces so the box wraps the text snugly.
            pdf.ln(8)
            ar_part = ar(label_ar)
            en_part = f" / {label_en}"
            set_font("B", 11)
            ar_w = pdf.get_string_width(ar_part)
            pdf.set_font("Helvetica", "B", 11)
            en_w = pdf.get_string_width(en_part)
            text_w = ar_w + en_w + 14
            stamp_x = pdf.l_margin
            stamp_y = pdf.get_y()
            pdf.set_draw_color(*stamp_rgb)
            pdf.set_text_color(*stamp_rgb)
            pdf.set_line_width(0.7)
            pdf.rect(stamp_x, stamp_y, text_w, 10, style="D")
            # Arabic chunk
            pdf.set_xy(stamp_x + 7, stamp_y + 2)
            set_font("B", 11)
            pdf.cell(ar_w, 6, ar_part)
            # Latin chunk
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(en_w, 6, en_part)
            pdf.set_y(stamp_y + 12)

            paid_at = payment.get("paid_at")
            method = payment.get("method")
            sub_bits = []
            if paid_at:
                sub_bits.append(str(paid_at))
            if method:
                sub_bits.append(str(method))
            if sub_bits:
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*INK_SOFT)
                pdf.set_x(stamp_x)
                pdf.cell(text_w, 4, " - ".join(sub_bits), align="L")
                pdf.ln(5)

            pdf.set_text_color(*INK)

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

        # Arabic compliance line — NotoArabic
        set_font("", 8)
        pdf.set_text_color(*INK_SOFT)
        pdf.cell(
            0,
            4,
            ar("هذه فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرائب المصرية"),
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        # English compliance line — Helvetica so the Latin glyphs render
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(
            0,
            4,
            "Electronic invoice issued per Egyptian Tax Authority requirements",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        # ── Powered by NUMU ─────────────────────────────────────────
        # Pure Latin — Helvetica throughout. "NUMU" gets the navy brand color
        # in bold, the surrounding text is a soft muted grey.
        pdf.ln(5)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MUTED)
        pre = "Powered by "
        brand = "NUMU"
        post = " - numuegapp"
        w_pre = pdf.get_string_width(pre)
        pdf.set_font("Helvetica", "B", 7)
        w_brand = pdf.get_string_width(brand)
        pdf.set_font("Helvetica", "", 7)
        w_post = pdf.get_string_width(post)
        total_w = w_pre + w_brand + w_post
        pdf.set_x((pdf.w - total_w) / 2)
        pdf.cell(w_pre, 4, pre)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*NAVY)
        pdf.cell(w_brand, 4, brand)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MUTED)
        pdf.cell(w_post, 4, post)

        return bytes(pdf.output())

    def _render_template(
        self,
        invoice: Invoice,
        payment: dict[str, Any] | None = None,
    ) -> str:
        """Render the Jinja2 HTML template with invoice data."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        template = env.get_template(self.template_name)
        context = self._build_context(invoice, payment=payment)
        return template.render(**context)

    def _resolve_logo_url(self) -> str | None:
        """Resolve the logo URL for the template.

        Order of preference:
            1. store_logo_url (per-merchant logo from store settings)
            2. Bundled NUMU SVG (vector — sharpest output)
            3. Bundled NUMU PNG (legacy raster fallback)
        """
        if self.store_logo_url:
            return self.store_logo_url
        if self._DEFAULT_LOGO.exists():
            return self._DEFAULT_LOGO.as_uri()
        if self._DEFAULT_LOGO_PNG.exists():
            return self._DEFAULT_LOGO_PNG.as_uri()
        return None

    def _build_context(
        self,
        invoice: Invoice,
        payment: dict[str, Any] | None = None,
    ) -> dict:
        """Build the template rendering context from an Invoice entity.

        Formats monetary values from cents to display currency,
        prepares QR code data URI, computes layout direction, and
        normalizes payment status into a stable CSS-class key.
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

        # Payment status (optional). Normalize enums / mixed casing into a
        # stable key matching the CSS classes and label dicts
        # (e.g. "PAID" / "PaymentStatus.PAID" -> "paid").
        payment_status_key = None
        payment_method = None
        paid_at = None
        if payment:
            raw_status = payment.get("status")
            if raw_status:
                payment_status_key = str(raw_status).split(".")[-1].lower()
            payment_method = payment.get("method")
            paid_at = payment.get("paid_at")

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
            # Formatted totals (cents -> display currency). The invoice
            # is rendered tax-free, so grand_total = subtotal - discount
            # (NOT invoice.total, which includes tax). The data layer
            # still carries total_taxes for ETA submission elsewhere.
            "subtotal": f"{invoice.subtotal / 100:,.2f}",
            "total_discount": f"{invoice.total_discount / 100:,.2f}",
            "total_taxes": f"{invoice.total_taxes / 100:,.2f}",
            "grand_total": f"{(invoice.subtotal - (invoice.total_discount or 0)) / 100:,.2f}",
            "currency": invoice.currency,
            # QR code
            "qr_data_uri": qr_data_uri,
            # Party info
            "seller": invoice.seller,
            "buyer": invoice.buyer,
            # Payment context (renders the colored stamp if non-null)
            "payment_status_key": payment_status_key,
            "payment_method": payment_method,
            "paid_at": paid_at,
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
    "payment": {
        "paid": "مدفوعة",
        "pending": "في انتظار الدفع",
        "unpaid": "غير مدفوعة",
        "authorized": "مصرّح بها",
        "partially_refunded": "مسترد جزئياً",
        "refunded": "تم الاسترداد",
        "failed": "فشل الدفع",
    },
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
    "payment": {
        "paid": "Paid",
        "pending": "Pending",
        "unpaid": "Unpaid",
        "authorized": "Authorized",
        "partially_refunded": "Partially Refunded",
        "refunded": "Refunded",
        "failed": "Failed",
    },
}
