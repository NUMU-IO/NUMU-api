"""Waybill (بوليصة) generation for manual carriers.

A Tier 3 courier has no API and issues no label, so NUMU prints the
parcel's identity itself: who it goes to, what to collect, and a number
both sides can quote.

**One layout, two outputs.** The label is authored once at 100×150mm —
the common Egyptian thermal roll — and printed either 1:1 on that roll or
tiled four-up on A4 for a merchant without a label printer. Both use the
same markup (``_label.html``) and the same stylesheet (``label.css``), so
the two can never drift into different labels.

Only the A4 sheet is scaled, to 99%: four 100×150mm labels tile to
200×300mm and A4 is 297mm tall. The roll is never scaled — a thermal
printer feeds fixed-width stock.

**Arabic.** This rides the PDF pipeline the invoice generator already
uses in production rather than building a second one: WeasyPrint renders
the HTML/CSS with real RTL, and Noto Sans Arabic — already installed in
``docker/fonts`` and registered via fontconfig in the image — provides
the glyphs. Zero infrastructure change. Addresses are Arabic; **tracking
numbers, phone numbers, the COD amount and dates are forced LTR**, because
a phone number reordered by the bidi algorithm is worse than useless on a
label a courier has to dial.

**Why a QR and not Code128.** For a Tier 3 courier nobody scans our
number but us — the courier uses their own paperwork or none. A QR
survives cheap thermal printing better, reads from a phone camera, and
needs no new dependency (``qrcode`` is already used for invoice QR). If a
courier ever needs a 1D symbology, add it alongside rather than replacing
this.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.
"""

import base64
import io
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

#: Thermal-roll wrapper: one label per page, printed 1:1.
ROLL_TEMPLATE = "waybill.html"
#: A4 wrapper: the same label tiled four to a page.
SHEET_TEMPLATE = "waybill_sheet.html"

#: The label's authored size — the common Egyptian thermal roll.
LABEL_SIZE = "100mm 150mm"

#: Labels per A4 sheet, in a 2x2 grid.
SHEET_PER_PAGE = 4

#: Four 100x150mm labels tile to 200x300mm; A4 is 297mm tall, so 1:1
#: overflows by 3mm. 99% lands on exactly 297mm and is imperceptible on
#: a printed label — well inside QR scanning tolerance. **The roll output
#: is never scaled**: a thermal printer feeds fixed-width stock, and
#: shrinking the label would walk it off its own roll.
SHEET_SCALE = 0.99

#: Items listed before the label runs out of room.
MAX_ITEMS = 6


def _qr_data_uri(payload: str) -> str:
    """QR as a data URI so the PDF needs no external fetch.

    Error correction M: enough redundancy to survive a smudged thermal
    print without making the modules too small to scan.
    """
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _format_money(cents: int, currency: str = "EGP") -> str:
    """Money for a label: grouped, two decimals, currency after.

    Deliberately **not** localized to Arabic-Indic digits. A courier
    reads this aloud and writes it on a receipt, and Latin digits are
    what every Egyptian courier's own paperwork uses.
    """
    amount = (cents or 0) / 100
    return f"{amount:,.2f} {currency}"


def build_context(
    *,
    tracking_number: str,
    store_name: str,
    recipient_name: str,
    address_line: str,
    cod_amount_cents: int = 0,
    currency: str = "EGP",
    recipient_phone: str | None = None,
    governorate: str | None = None,
    order_number: str | None = None,
    created_at: str | None = None,
    courier_name: str = "",
    cutoff_time: str | None = None,
    items: list[dict[str, Any]] | None = None,
    notes: str | None = None,
    store_logo_url: str | None = None,
) -> dict[str, Any]:
    """Assemble one label's data, with the label's limits applied."""
    all_items = items or []
    shown = all_items[:MAX_ITEMS]

    return {
        "tracking_number": tracking_number,
        "qr_data_uri": _qr_data_uri(tracking_number),
        "store_name": store_name,
        "store_logo_url": store_logo_url,
        "courier_name": courier_name,
        "cutoff_time": cutoff_time,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "address_line": address_line,
        "governorate": governorate,
        "order_number": order_number,
        "created_at": created_at,
        # A zero or missing COD is "paid", not "collect 0.00" — a courier
        # shown 0.00 EGP asks the customer for money.
        "is_cod": bool(cod_amount_cents and cod_amount_cents > 0),
        "cod_display": _format_money(cod_amount_cents, currency),
        # Named line_items, not items: `label.items` in a Jinja template
        # resolves to dict.items() rather than this key.
        "line_items": shown,
        "extra_items": max(0, len(all_items) - len(shown)),
        "notes": notes,
    }


def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def render_html(labels: list[dict[str, Any]] | dict[str, Any]) -> str:
    """Render labels for a thermal roll — one per page, 1:1."""
    if isinstance(labels, dict):
        labels = [labels]
    return (
        _env().get_template(ROLL_TEMPLATE).render(labels=labels, page_size=LABEL_SIZE)
    )


def render_sheet_html(
    labels: list[dict[str, Any]], *, per_page: int = SHEET_PER_PAGE
) -> str:
    """Render the same labels four-up on A4, with cut guides."""
    pages = [labels[i : i + per_page] for i in range(0, len(labels), per_page)] or [[]]
    return (
        _env()
        .get_template(SHEET_TEMPLATE)
        .render(
            pages=pages,
            per_page=per_page,
            sheet_scale=SHEET_SCALE,
            sheet_title=f"{len(labels)} labels",
        )
    )


def _to_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # pragma: no cover - env dependent
        raise WaybillRenderError(
            "WeasyPrint is required to render waybills (needs cairo/pango). "
            "It is installed in the Docker image; on local Windows use the "
            "HTML preview instead."
        ) from e
    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def generate_waybill_pdf(context: dict[str, Any]) -> bytes:
    """One label on a thermal roll."""
    pdf = _to_pdf(render_html([context]))
    logger.info(
        "waybill_pdf_generated",
        extra={
            "tracking_number": context.get("tracking_number"),
            "pdf_size_bytes": len(pdf),
        },
    )
    return pdf


def generate_waybill_batch_pdf(contexts: list[dict[str, Any]]) -> bytes:
    """A day's pickup on a thermal roll, one label per page.

    Merchants ship in batches; sending them to print one label at a time
    is how labels get missed.
    """
    if not contexts:
        raise WaybillRenderError("No shipments to print.")
    pdf = _to_pdf(render_html(contexts))
    logger.info(
        "waybill_batch_generated",
        extra={"count": len(contexts), "pdf_size_bytes": len(pdf)},
    )
    return pdf


def generate_waybill_sheet_pdf(contexts: list[dict[str, Any]]) -> bytes:
    """The same labels on A4, four to a page, for an office printer.

    Same markup and stylesheet as the roll output, so the two can never
    drift into different labels.
    """
    if not contexts:
        raise WaybillRenderError("No shipments to print.")
    pdf = _to_pdf(render_sheet_html(contexts))
    logger.info(
        "waybill_sheet_generated",
        extra={"count": len(contexts), "pdf_size_bytes": len(pdf)},
    )
    return pdf


class WaybillRenderError(RuntimeError):
    """Raised when a waybill cannot be rendered."""


__all__ = [
    "LABEL_SIZE",
    "MAX_ITEMS",
    "ROLL_TEMPLATE",
    "SHEET_PER_PAGE",
    "SHEET_SCALE",
    "SHEET_TEMPLATE",
    "WaybillRenderError",
    "build_context",
    "generate_waybill_batch_pdf",
    "generate_waybill_pdf",
    "generate_waybill_sheet_pdf",
    "render_html",
    "render_sheet_html",
]
