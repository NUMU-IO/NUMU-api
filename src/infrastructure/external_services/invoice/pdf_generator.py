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
        INFO = _hex_to_rgb(_s["info"])  # #1F4A7A — Navy 600

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

        def _script_runs(text: str):
            """Split a string into ``(chunk, is_arabic)`` runs.

            Punctuation, digits, and ASCII spaces stick to whichever side
            they are adjacent to so addresses like ``"12، شارع التحرير،
            Cairo"`` produce three runs rather than character-by-character
            jitter. Used by ``render_value_right_aligned`` to render mixed-
            script values without missing-glyph warnings from the Arabic
            font when it hits Latin chars.
            """
            if not text:
                return []
            # Whitespace + Arabic-specific punctuation stay with whichever
            # script they're embedded in. Latin punctuation ( ) % # [ ] etc.
            # is excluded — it forms its own Latin run so the Arabic font
            # never has to render an ASCII glyph it doesn't ship.
            STICKY = set("  ،؛")
            runs: list[list] = []  # [[chars, is_arabic], ...]
            for ch in text:
                is_ar = bool(_ARABIC_RE.search(ch))
                if not runs:
                    runs.append([ch, is_ar])
                    continue
                last = runs[-1]
                if ch in STICKY or is_ar == last[1]:
                    last[0] += ch
                else:
                    runs.append([ch, is_ar])
            return [(r[0], r[1]) for r in runs]

        # Arabic Presentation Forms ranges (post-reshape) + the base
        # Arabic block — any char in these ranges needs the Arabic font.
        _ARABIC_FONT_RE = _re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

        def _measure_runs(
            text: str, size: float, style: str = ""
        ) -> list[tuple[str, bool, float]]:
            """Process the whole string through arabic-reshaper + python-bidi
            (which handles RTL reordering AND paren mirroring), then split
            the bidi-output by *Unicode block* for font selection.

            By doing the bidi pass on the FULL string we get the correct
            visual order for sequences like ``"إجمالي المنتجات (شامل الضريبة)"``
            — the parens are mirrored, the Arabic words are reversed, and
            "14%" stays as a digit group in its proper visual position.
            Then we just paint each script-uniform chunk left-to-right.

            Returns ``[(chunk_in_visual_order, needs_arabic_font, width_mm), ...]``.
            """
            if not text:
                return []
            # Run bidi once on the whole string — this is what ``ar()`` does
            # for a single chunk, but applied at the full-label level so
            # cross-script ordering / paren mirroring resolve correctly.
            reshaped = arabic_reshaper.reshape(text)
            visual = get_display(reshaped)

            # Split by font requirement: consecutive Arabic-block chars
            # become one chunk (rendered with NotoArabic); consecutive
            # non-Arabic chars (Latin letters, ASCII digits, ``()``, ``%``)
            # become another (rendered with Helvetica).
            out: list[tuple[str, bool, float]] = []
            cur_chunk: list[str] = []
            cur_is_ar: bool | None = None
            for ch in visual:
                is_ar_char = bool(_ARABIC_FONT_RE.match(ch))
                if cur_is_ar is None:
                    cur_is_ar = is_ar_char
                    cur_chunk.append(ch)
                elif is_ar_char == cur_is_ar:
                    cur_chunk.append(ch)
                else:
                    chunk_str = "".join(cur_chunk)
                    if cur_is_ar and has_arabic_font:
                        pdf.set_font("NotoArabic", style, size)
                    else:
                        pdf.set_font("Helvetica", style, size)
                    out.append((
                        chunk_str,
                        bool(cur_is_ar),
                        pdf.get_string_width(chunk_str),
                    ))
                    cur_chunk = [ch]
                    cur_is_ar = is_ar_char
            if cur_chunk:
                chunk_str = "".join(cur_chunk)
                if cur_is_ar and has_arabic_font:
                    pdf.set_font("NotoArabic", style, size)
                else:
                    pdf.set_font("Helvetica", style, size)
                out.append((
                    chunk_str,
                    bool(cur_is_ar),
                    pdf.get_string_width(chunk_str),
                ))
            return out

        def render_mixed_text(
            x: float,
            y: float,
            w: float,
            h: float,
            text: str,
            size: float = 8,
            style: str = "",
            align: str = "L",
        ) -> None:
            """Render a (possibly mixed-script) string within
            ``(x, y, w, h)`` honoring ``align`` in {L, R, C}. Each script
            run renders with its proper font so Latin punctuation
            (``(``, ``)``, ``%``) shows up correctly inside otherwise-
            Arabic phrases.

            BiDi: when *any* Arabic run is present we treat the base
            direction as RTL and reverse the run order before painting
            them left-to-right. That way an Arabic reader scanning
            right-to-left encounters the logical first run first, then
            the parenthetical, then the closing paren — matching the
            way the phrase reads on paper. Without this reversal,
            ``"إجمالي المنتجات (شامل الضريبة)"`` would render with the
            parenthetical visually displaced to the left of the main
            phrase, even though it's logically meant to follow it.
            """
            measured = _measure_runs(text, size, style)
            if not measured:
                return

            # ``_measure_runs`` already returned chunks in visual LTR
            # order (bidi handled RTL reversal + paren mirroring on the
            # whole string). We just paint left-to-right at increasing X.
            total_w = sum(w_ for _, _, w_ in measured)
            # If we need to truncate, drop from the visually-leftmost
            # end (which is the *logical end* of an RTL phrase — the
            # qualifier / parenthetical we can lose first).
            while total_w > w and len(measured) > 1:
                _, _, drop_w = measured.pop(0)
                total_w -= drop_w
            if align == "R":
                cur_x = x + w - total_w
            elif align == "C":
                cur_x = x + (w - total_w) / 2
            else:
                cur_x = x
            for chunk, is_ar, chunk_w in measured:
                if is_ar:
                    pdf.set_font("NotoArabic", style, size)
                else:
                    pdf.set_font("Helvetica", style, size)
                pdf.set_xy(cur_x, y)
                pdf.cell(chunk_w + 0.2, h, chunk)
                cur_x += chunk_w

        def render_value_right_aligned(
            x: float, y: float, w: float, h: float, val: str, size: float = 8
        ) -> None:
            """Backwards-compatible shorthand for right-aligned mixed text."""
            render_mixed_text(x, y, w, h, val, size=size, align="R")

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

        # ════════════════════════════════════════════════════════════
        # ── Header: balanced three-column composition ───────────────
        # Logo (left) · Bilingual title (center) · Meta (right). Hairline
        # navy rule below sets the visual horizon for the rest of the page.
        # ════════════════════════════════════════════════════════════
        logo_png = self._DEFAULT_LOGO_PNG
        header_top_y = pdf.get_y()
        logo_height = 14  # mm
        logo_max_w = 40
        if logo_png.exists():
            try:
                pdf.image(str(logo_png), x=pdf.l_margin, y=header_top_y, h=logo_height)
            except Exception:  # pragma: no cover — never fatal
                logger.warning("fpdf2_logo_embed_failed", extra={"path": str(logo_png)})

        # Title block — Arabic primary, English tracking subtitle below
        title_y = header_top_y + 1
        pdf.set_xy(pdf.l_margin + logo_max_w, title_y)
        set_font("B", 22)
        pdf.set_text_color(*NAVY)
        pdf.cell(pw - logo_max_w * 2, 9, ar("فاتورة ضريبية"), align="C")
        pdf.set_xy(pdf.l_margin + logo_max_w, title_y + 9)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        # Letter-spacing fake: insert thin spaces between chars
        pdf.cell(pw - logo_max_w * 2, 4, "T A X   I N V O I C E", align="C")
        pdf.set_text_color(*INK)

        # Meta column (top-right) — hairline-separated rows
        meta_x = pdf.w - pdf.r_margin - 50
        meta_y = header_top_y
        meta_rows = [
            ("INVOICE", invoice.invoice_number),
            ("DATE", invoice.date_issued.strftime("%Y-%m-%d")),
            ("CURRENCY", invoice.currency),
        ]
        if invoice.invoice_type and invoice.invoice_type.value != "I":
            meta_rows.append(("TYPE", invoice.invoice_type.value))

        for i, (lbl, val) in enumerate(meta_rows):
            row_y = meta_y + i * 5
            # Hairline divider between rows (skip the first)
            if i > 0:
                pdf.set_draw_color(*BORDER)
                pdf.set_line_width(0.15)
                pdf.line(meta_x, row_y, pdf.w - pdf.r_margin, row_y)
            pdf.set_xy(meta_x, row_y + 0.8)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*MUTED)
            pdf.cell(20, 4, lbl)
            pdf.set_xy(meta_x + 18, row_y + 0.6)
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*INK)
            pdf.cell(32, 4, str(val), align="R")

        # Hairline navy rule under the header
        header_bottom = max(
            header_top_y + logo_height + 4,
            title_y + 14,
            meta_y + len(meta_rows) * 5 + 1,
        )
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin, header_bottom, pdf.w - pdf.r_margin, header_bottom)
        pdf.set_y(header_bottom + 8)

        # ════════════════════════════════════════════════════════════
        # ── Seller & Buyer cards — equal-weight cream cards with a
        #    thin navy accent rail on the RIGHT edge (RTL convention).
        # ════════════════════════════════════════════════════════════
        seller = invoice.seller
        buyer = invoice.buyer

        card_gap = 8
        card_w = (pw - card_gap) / 2
        card_h = 44
        card_x_left = pdf.l_margin
        card_x_right = pdf.l_margin + card_w + card_gap
        card_y = pdf.get_y()

        def _draw_party_card(
            cx: float,
            cy: float,
            party,
            eyebrow_ar: str,
            eyebrow_en: str,
        ) -> None:
            inner_x = cx + 5
            inner_w = card_w - 10
            inner_y = cy + 5

            # Card background + thin border
            pdf.set_fill_color(*SURFACE)
            pdf.set_draw_color(*BORDER)
            pdf.set_line_width(0.2)
            pdf.rect(cx, cy, card_w, card_h, style="DF")
            # Navy accent rail on the right edge (RTL "leading edge")
            pdf.set_fill_color(*NAVY)
            pdf.rect(cx + card_w - 1.5, cy + 4, 1.5, 8, style="F")

            # Eyebrow ("البائع · SELLER")
            pdf.set_xy(inner_x, inner_y)
            set_font("B", 7)
            pdf.set_text_color(*NAVY)
            ar_label = ar(eyebrow_ar)
            ar_w = pdf.get_string_width(ar_label)
            pdf.cell(ar_w, 3.5, ar_label)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*MUTED)
            pdf.cell(inner_w - ar_w, 3.5, f"  ·  {eyebrow_en}")

            # Primary name (large, ink)
            name_y = inner_y + 5
            primary = party.name_ar or party.name or ""
            pdf.set_xy(inner_x, name_y)
            pdf.set_text_color(*INK)
            if has_arabic(primary):
                set_font("B", 11)
                pdf.cell(inner_w, 5, ar(primary))
            else:
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(inner_w, 5, primary)

            # Secondary name (smaller, soft)
            secondary = party.name if party.name and party.name != primary else None
            rows_y = name_y + 5.5
            if secondary:
                pdf.set_xy(inner_x, rows_y)
                pdf.set_text_color(*INK_SOFT)
                if has_arabic(secondary):
                    set_font("", 8)
                    pdf.cell(inner_w, 4, ar(secondary))
                else:
                    pdf.set_font("Helvetica", "", 8)
                    pdf.cell(inner_w, 4, secondary)
                rows_y += 4.5

            # Detail rows — label (muted, small) on the left, value (ink) on the right
            details = []
            if party.tax_id:
                details.append((ar("الرقم الضريبي"), "Tax ID", party.tax_id))
            elif getattr(party, "national_id", None):
                details.append((ar("رقم الهوية"), "National ID", party.national_id))
            addr_parts = []
            if party.building_number:
                addr_parts.append(str(party.building_number))
            if party.street:
                addr_parts.append(str(party.street))
            if party.city:
                addr_parts.append(str(party.city))
            if getattr(party, "governorate", None):
                addr_parts.append(str(party.governorate))
            if addr_parts:
                details.append((ar("العنوان"), "Address", ", ".join(addr_parts)))
            if getattr(party, "phone", None):
                details.append((ar("الهاتف"), "Phone", party.phone))

            for j, (lbl_ar, lbl_en, val) in enumerate(details[:3]):
                row_y = rows_y + j * 5
                # Hairline divider between rows (no top rule for the first)
                if j > 0:
                    pdf.set_draw_color(*BORDER)
                    pdf.set_line_width(0.1)
                    pdf.line(inner_x, row_y - 0.5, inner_x + inner_w, row_y - 0.5)

                # ── Label cluster on the LEFT (label_ar · label_en) ─
                pdf.set_xy(inner_x, row_y + 0.6)
                pdf.set_text_color(*MUTED)
                set_font("", 7)
                lbl_ar_w = pdf.get_string_width(lbl_ar) + 0.5
                pdf.cell(lbl_ar_w, 4, lbl_ar)
                pdf.set_font("Helvetica", "", 6.5)
                sep_text = f"  ·  {lbl_en}"
                sep_w = pdf.get_string_width(sep_text) + 0.5
                pdf.cell(sep_w, 4, sep_text)

                # ── Value on the RIGHT, mixed-script aware ──────────
                pdf.set_text_color(*INK)
                value_w = max(8.0, inner_w - lbl_ar_w - sep_w)
                value_x = inner_x + lbl_ar_w + sep_w
                render_value_right_aligned(
                    x=value_x,
                    y=row_y + 0.4,
                    w=value_w,
                    h=4,
                    val=str(val),
                    size=8,
                )

        _draw_party_card(card_x_left, card_y, seller, "البائع", "SELLER")
        _draw_party_card(card_x_right, card_y, buyer, "المشتري", "BUYER")

        pdf.set_y(card_y + card_h + 10)

        # ════════════════════════════════════════════════════════════
        # ── Line items table ────────────────────────────────────────
        # Premium table: 12mm navy header band with AR primary + uppercase
        # EN subtitle in tracking; cream alternating rows; thin bone-
        # color separators; no border on the outer frame (the navy band
        # carries the visual weight).
        # ════════════════════════════════════════════════════════════
        col_w = [10, 78, 22, 26, 20, 18]  # sums to 174 (= pw with 18mm margins)
        col_names_ar = ["#", "المنتج", "الكمية", "سعر الوحدة", "الخصم", "الإجمالي"]
        col_names_en = ["", "Product", "Qty", "Unit Price", "Discount", "Total"]
        col_align = ["C", "R", "C", "R", "R", "R"]

        table_x = pdf.l_margin
        table_top = pdf.get_y()
        header_h = 13

        # Navy header band (full width)
        pdf.set_fill_color(*NAVY)
        pdf.rect(table_x, table_top, pw, header_h, style="F")

        # Header text — AR primary (top), EN tracking subtitle (bottom)
        x_cursor = table_x
        for i, (h_ar, h_en) in enumerate(zip(col_names_ar, col_names_en)):
            cell_w = col_w[i]
            pdf.set_text_color(*SURFACE)
            # Arabic primary (centered vertically in top half). The
            # ``#`` column has no Arabic glyph so it renders with
            # Helvetica via the script-aware renderer.
            if h_ar == "#":
                pdf.set_xy(x_cursor, table_top + 3.6)
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(cell_w, 4.5, "#", align=col_align[i])
            else:
                pdf.set_xy(x_cursor, table_top + 1.6)
                set_font("B", 8.5)
                pdf.cell(cell_w, 4.5, ar(h_ar), align=col_align[i])
            # English subtitle, uppercase + tracked
            if h_en:
                pdf.set_xy(x_cursor, table_top + 7.2)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(196, 210, 225)  # Navy 200 — soft on navy bg
                # Insert thin spaces to fake letter-spacing
                tracked = "  ".join(h_en.upper())
                pdf.cell(cell_w, 4, tracked, align=col_align[i])
            x_cursor += cell_w

        # Rows
        row_h = 12
        cursor_y = table_top + header_h
        for idx, item in enumerate(invoice.line_items, 1):
            # Subtle zebra
            if idx % 2 == 0:
                pdf.set_fill_color(*SURFACE)
                pdf.rect(table_x, cursor_y, pw, row_h, style="F")

            # Row separator (bone hairline)
            if idx > 1:
                pdf.set_draw_color(*BORDER)
                pdf.set_line_width(0.15)
                pdf.line(table_x, cursor_y, table_x + pw, cursor_y)

            x_cursor = table_x
            pdf.set_text_color(*INK)

            # #
            pdf.set_xy(x_cursor, cursor_y + 4)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*MUTED)
            pdf.cell(col_w[0], 4, str(idx), align="C")
            x_cursor += col_w[0]

            # Product (AR primary, EN sub-line if both)
            desc_ar = item.description_ar or ""
            desc_en = item.description or ""
            if desc_ar and desc_en and desc_ar != desc_en:
                # Stack: AR top, EN below in smaller muted
                pdf.set_xy(x_cursor + 2, cursor_y + 2.5)
                pdf.set_text_color(*INK)
                if has_arabic(desc_ar):
                    set_font("B", 9.5)
                    text = ar(desc_ar)
                    if len(text) > 40:
                        text = text[:37] + "..."
                    pdf.cell(col_w[1] - 4, 4, text, align="R")
                else:
                    pdf.set_font("Helvetica", "B", 9.5)
                    text = desc_ar
                    if len(text) > 40:
                        text = text[:37] + "..."
                    pdf.cell(col_w[1] - 4, 4, text, align="L")
                # EN sub-line
                pdf.set_xy(x_cursor + 2, cursor_y + 6.5)
                pdf.set_font("Helvetica", "", 7.5)
                pdf.set_text_color(*MUTED)
                en_text = desc_en
                if len(en_text) > 50:
                    en_text = en_text[:47] + "..."
                pdf.cell(col_w[1] - 4, 4, en_text, align="L")
            else:
                # Single-language line, vertically centered
                desc = desc_ar or desc_en
                pdf.set_xy(x_cursor + 2, cursor_y + 4)
                pdf.set_text_color(*INK)
                if has_arabic(desc):
                    set_font("B", 9.5)
                    text = ar(desc)
                    if len(text) > 40:
                        text = text[:37] + "..."
                    pdf.cell(col_w[1] - 4, 4, text, align="R")
                else:
                    pdf.set_font("Helvetica", "B", 9.5)
                    text = desc
                    if len(text) > 40:
                        text = text[:37] + "..."
                    pdf.cell(col_w[1] - 4, 4, text, align="L")
            x_cursor += col_w[1]

            # Numeric columns (qty / unit_price / discount / total)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", "", 9.5)
            numeric_cols = [
                (f"{item.quantity:,.0f}", col_w[2], "C"),
                (f"{item.unit_price:,.2f}", col_w[3], "R"),
                (f"{item.discount:,.2f}", col_w[4], "R"),
                (f"{item.net_total:,.2f}", col_w[5], "R"),
            ]
            for text, w, align in numeric_cols:
                pdf.set_xy(x_cursor, cursor_y + 4)
                pdf.cell(w, 4, text, align=align)
                x_cursor += w

            cursor_y += row_h

        # Bottom rule of the table
        pdf.set_draw_color(*NAVY)
        pdf.set_line_width(0.3)
        pdf.line(table_x, cursor_y, table_x + pw, cursor_y)
        pdf.set_y(cursor_y + 10)

        # ════════════════════════════════════════════════════════════
        # ── Totals — premium card on the LEFT (RTL: "end" side) ────
        # Rounded-look cream card with hairline row separators. Grand
        # total sits above a thicker navy rule. Subtotal · VAT · Ship·
        # Discount rows in soft grey; Grand Total in deep navy.
        # ════════════════════════════════════════════════════════════
        totals_w = 96
        totals_x = pdf.l_margin
        totals_y = pdf.get_y()

        # Build rows first so we can size the card height precisely
        rows: list[tuple[str, str, str, bool]] = []
        rows.append((
            "إجمالي المنتجات (شامل الضريبة)",
            "Products Total (VAT incl.)",
            f"{invoice.subtotal / 100:,.2f} {invoice.currency}",
            False,
        ))
        rows.append((
            "ض.ق.م 14% (مضمنة)",
            "VAT 14% (Included)",
            f"{invoice.vat_amount / 100:,.2f} {invoice.currency}",
            False,
        ))
        if invoice.total_discount:
            rows.append((
                "إجمالي الخصم",
                "Discount",
                f"-{invoice.total_discount / 100:,.2f} {invoice.currency}",
                False,
            ))
        rows.append((
            "رسوم الشحن",
            "Shipping",
            f"{invoice.shipping_fee / 100:,.2f} {invoice.currency}",
            False,
        ))
        rows.append((
            "الإجمالي النهائي",
            "GRAND TOTAL",
            f"{invoice.grand_total / 100:,.2f} {invoice.currency}",
            True,
        ))

        row_h = 11
        card_pad = 4
        card_h = card_pad * 2 + row_h * len(rows)

        # Card surface
        pdf.set_fill_color(*SURFACE)
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.2)
        pdf.rect(totals_x, totals_y, totals_w, card_h, style="DF")

        for i, (lbl_ar, lbl_en, val, is_grand) in enumerate(rows):
            ry = totals_y + card_pad + i * row_h
            inner_x = totals_x + 8
            inner_w = totals_w - 16

            # Row divider above (skip first; thicker rule above grand total)
            if i > 0:
                if is_grand:
                    pdf.set_draw_color(*NAVY)
                    pdf.set_line_width(0.4)
                else:
                    pdf.set_draw_color(*BORDER)
                    pdf.set_line_width(0.15)
                pdf.line(totals_x + 6, ry, totals_x + totals_w - 6, ry)

            # ── Label cluster: AR primary (top), EN sub-line (below) ─
            label_color = NAVY if is_grand else INK_SOFT
            en_color = NAVY if is_grand else MUTED
            ar_size = 10 if is_grand else 9
            en_size = 7.5 if is_grand else 6.5

            # Mixed-script aware: labels like "ض.ق.م 14% (مضمنة)" have
            # ASCII punctuation that the Arabic font can't render — split
            # by script and render each run with its proper font.
            pdf.set_text_color(*label_color)
            label_style = "B" if is_grand else ""
            render_mixed_text(
                x=inner_x,
                y=ry + 1,
                w=inner_w * 0.7,
                h=4.5,
                text=lbl_ar,
                size=ar_size,
                style=label_style,
                align="L",
            )

            # ── Value, right-aligned ─────────────────────────────────
            val_color = NAVY if is_grand else INK
            val_size = 13 if is_grand else 10
            pdf.set_font("Helvetica", "B" if is_grand else "", val_size)
            pdf.set_text_color(*val_color)
            pdf.set_xy(inner_x, ry + 1)
            pdf.cell(inner_w, 4.5, val, align="R")

            # EN sub-line directly under the AR label
            pdf.set_xy(inner_x, ry + 6)
            pdf.set_font("Helvetica", "B" if is_grand else "", en_size)
            pdf.set_text_color(*en_color)
            en_label = lbl_en
            if is_grand:
                en_label = "  ".join(en_label)  # fake letter-spacing
            pdf.cell(inner_w, 3.5, en_label)

        # ── VAT-included notice — sits right under the totals card ──
        notice_w = totals_w
        notice_y = totals_y + card_h + 4
        notice_h = 11
        pdf.set_fill_color(*SURFACE)
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.2)
        pdf.rect(totals_x, notice_y, notice_w, notice_h, style="DF")
        # Navy accent rail on the right (RTL leading edge)
        pdf.set_fill_color(*NAVY)
        pdf.rect(totals_x + notice_w - 1.5, notice_y + 2, 1.5, notice_h - 4, style="F")
        # Arabic primary — render via mixed-script helper so the "14"
        # and "%" land in Helvetica rather than the Arabic font.
        pdf.set_text_color(*NAVY)
        render_mixed_text(
            x=totals_x,
            y=notice_y + 1.5,
            w=notice_w - 3,
            h=4,
            text="الأسعار شاملة ضريبة القيمة المضافة 14%",
            size=8.5,
            style="B",
            align="C",
        )
        # English subtitle, tracked
        pdf.set_xy(totals_x, notice_y + 5.8)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(notice_w - 3, 3.5, "  ".join("PRICES INCLUDE 14% VAT"), align="C")

        # ════════════════════════════════════════════════════════════
        # ── Payment pill — rounded capsule (no rotation) on the RIGHT
        # ════════════════════════════════════════════════════════════
        if payment and payment.get("status"):
            raw_status = payment.get("status")
            status_key = str(raw_status).split(".")[-1].lower()

            pill_color_map = {
                "paid": (92, 123, 90),  # Sage
                "pending": (197, 138, 31),  # Saffron
                "unpaid": (197, 138, 31),
                "authorized": INFO,
                "partially_refunded": (180, 66, 24),
                "refunded": (180, 66, 24),
                "failed": (180, 66, 24),
            }
            pill_rgb = pill_color_map.get(status_key, MUTED)
            label_ar = _LABELS_AR["payment"].get(status_key, status_key)
            label_en = _LABELS_EN["payment"].get(status_key, status_key)

            # Measure widths
            ar_text = ar(label_ar)
            set_font("B", 10)
            ar_w = pdf.get_string_width(ar_text)
            pdf.set_font("Helvetica", "", 7)
            en_w = pdf.get_string_width(label_en.upper())
            text_w = max(ar_w, en_w)

            # Optional metadata (paid_at + method) on the right of the pill
            paid_at = payment.get("paid_at")
            method = payment.get("method")
            meta_bits = []
            if paid_at:
                meta_bits.append(str(paid_at))
            if method:
                meta_bits.append(str(method))
            meta_text = " · ".join(meta_bits)
            pdf.set_font("Helvetica", "", 7)
            meta_w = pdf.get_string_width(meta_text) if meta_text else 0

            # Layout: [dot] [AR / EN stack] [hairline] [meta]
            dot_d = 3
            pad = 6
            gap = 4
            divider_w = 1 if meta_text else 0
            pill_w = (
                pad
                + dot_d
                + gap
                + text_w
                + (gap + divider_w + gap + meta_w if meta_text else 0)
                + pad
            )
            pill_h = 11
            pill_x = pdf.w - pdf.r_margin - pill_w
            pill_y = notice_y + notice_h + 8

            # Capsule body — fake rounded look with cream fill + colored stroke
            pdf.set_fill_color(*SURFACE)
            pdf.set_draw_color(*pill_rgb)
            pdf.set_line_width(0.4)
            pdf.rect(pill_x, pill_y, pill_w, pill_h, style="DF")
            # End caps (semicircles approximated by small dark filled rects)
            cap_size = 1
            pdf.set_fill_color(*pill_rgb)
            pdf.rect(
                pill_x - cap_size,
                pill_y + pill_h / 2 - cap_size,
                cap_size,
                cap_size * 2,
                style="F",
            )
            pdf.rect(
                pill_x + pill_w,
                pill_y + pill_h / 2 - cap_size,
                cap_size,
                cap_size * 2,
                style="F",
            )

            # Status dot (filled circle)
            dot_x = pill_x + pad
            dot_y = pill_y + (pill_h - dot_d) / 2
            pdf.set_fill_color(*pill_rgb)
            pdf.ellipse(dot_x, dot_y, dot_d, dot_d, style="F")

            # AR / EN stack
            text_x = dot_x + dot_d + gap
            pdf.set_xy(text_x, pill_y + 1.5)
            set_font("B", 9.5)
            pdf.set_text_color(*pill_rgb)
            pdf.cell(text_w, 4, ar_text, align="L")
            pdf.set_xy(text_x, pill_y + 5.8)
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.cell(text_w, 3.5, "  ".join(label_en.upper()), align="L")

            # Optional meta on the right
            if meta_text:
                div_x = text_x + text_w + gap
                pdf.set_draw_color(*BORDER)
                pdf.set_line_width(0.2)
                pdf.line(div_x, pill_y + 2, div_x, pill_y + pill_h - 2)
                pdf.set_xy(div_x + gap, pill_y + (pill_h - 3) / 2)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*INK_SOFT)
                pdf.cell(meta_w, 3, meta_text, align="L")

            pdf.set_y(pill_y + pill_h + 4)
            pdf.set_text_color(*INK)
        else:
            pdf.set_y(notice_y + notice_h + 8)

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

        # ════════════════════════════════════════════════════════════
        # ── ETA compliance footer + Powered by ──────────────────────
        # ════════════════════════════════════════════════════════════
        pdf.ln(10)
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(5)

        # Arabic compliance line
        set_font("", 8.5)
        pdf.set_text_color(*INK_SOFT)
        pdf.cell(
            0,
            4,
            ar("فاتورة إلكترونية معتمدة من مصلحة الضرائب المصرية"),
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        # English compliance line — uppercase + tracked
        pdf.ln(1.5)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(
            0,
            4,
            "  ".join("EGYPTIAN TAX AUTHORITY COMPLIANT E-INVOICE"),
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        # Powered by NUMU — brand line, tracked
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*MUTED)
        pre = "Powered by "
        brand = "N U M U"
        w_pre = pdf.get_string_width(pre)
        pdf.set_font("Helvetica", "B", 7.5)
        w_brand = pdf.get_string_width(brand)
        total_w = w_pre + w_brand
        pdf.set_x((pdf.w - total_w) / 2)
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(w_pre, 4, pre)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*NAVY)
        pdf.cell(w_brand, 4, brand)

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
            # Formatted totals (cents -> display currency). Under the
            # VAT-inclusive model: ``subtotal`` already contains VAT;
            # ``vat_amount`` is the VAT extracted from it for the
            # template's "VAT included" line; ``grand_total`` adds
            # shipping on top of the inclusive subtotal.
            "prices_include_vat": invoice.prices_include_vat,
            "vat_rate": f"{invoice.vat_rate:.0f}",
            "subtotal": f"{invoice.subtotal / 100:,.2f}",
            "vat_amount": f"{invoice.vat_amount / 100:,.2f}",
            "net_amount_before_vat": f"{invoice.net_amount_before_vat / 100:,.2f}",
            "shipping_fee": f"{invoice.shipping_fee / 100:,.2f}",
            "total_discount": f"{invoice.total_discount / 100:,.2f}",
            "total_taxes": f"{invoice.vat_amount / 100:,.2f}",
            "grand_total": f"{invoice.grand_total / 100:,.2f}",
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
