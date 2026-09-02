"""Waybill (بوليصة) generation for manual carriers.

A Tier 3 courier has no API and issues no label, so NUMU prints the
parcel's identity itself: who it goes to, what to collect, and a number
both sides can quote.

**Format.** A6, 105×148mm. The common Egyptian thermal roll is 100×150mm
and A6 also prints sanely 4-up on an A4 sheet, which is what a merchant
without a label printer will use. It is a constant here rather than a
hardcoded value in the template — see :data:`LABEL_SIZE`.

**Arabic.** Same approach as the invoice generator: WeasyPrint renders
the HTML/CSS with real RTL, and Noto Sans Arabic (already installed in
``docker/fonts``) provides the glyphs. Addresses are Arabic; **tracking
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
TEMPLATE_NAME = "waybill.html"

#: A6. Change here, not in the stylesheet.
LABEL_SIZE = "105mm 148mm"

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
    """Assemble the template context, with the label's limits applied."""
    all_items = items or []
    shown = all_items[:MAX_ITEMS]

    return {
        "page_size": LABEL_SIZE,
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
        "items": shown,
        "extra_items": max(0, len(all_items) - len(shown)),
        "notes": notes,
    }


def render_html(context: dict[str, Any]) -> str:
    """Render the waybill template.

    Autoescaping is on: addresses and notes are merchant- and
    customer-supplied, and this HTML goes through a renderer.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template(TEMPLATE_NAME).render(**context)


def generate_waybill_pdf(context: dict[str, Any]) -> bytes:
    """Render one waybill to PDF bytes.

    Raises:
        WaybillRenderError: WeasyPrint is unavailable. Unlike the invoice
            generator there is no fpdf2 fallback yet — a label is a
            precise layout and a second implementation that drifts from
            this one would put the COD amount in the wrong place. Local
            Windows dev without Cairo gets a clear error rather than a
            differently-wrong PDF.
    """
    html = render_html(context)
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # pragma: no cover - env dependent
        raise WaybillRenderError(
            "WeasyPrint is required to render waybills (needs cairo/pango). "
            "It is installed in the Docker image; on local Windows use the "
            "HTML preview endpoint instead."
        ) from e

    pdf: bytes = HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()
    logger.info(
        "waybill_pdf_generated",
        extra={
            "tracking_number": context.get("tracking_number"),
            "pdf_size_bytes": len(pdf),
        },
    )
    return pdf


def generate_waybill_batch_pdf(contexts: list[dict[str, Any]]) -> bytes:
    """One PDF, one page per parcel — the day's pickup in a single print.

    Merchants ship in batches; sending them to print one label at a time
    is how labels get missed.
    """
    if not contexts:
        raise WaybillRenderError("No shipments to print.")

    pages = [render_html(c) for c in contexts]
    # Concatenate bodies with a hard page break so pagination stays under
    # the stylesheet's control rather than the renderer's.
    joined = '<div style="break-after:page"></div>'.join(pages)

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:  # pragma: no cover - env dependent
        raise WaybillRenderError(
            "WeasyPrint is required to render waybills (needs cairo/pango)."
        ) from e

    pdf: bytes = HTML(string=joined, base_url=str(TEMPLATE_DIR)).write_pdf()
    logger.info(
        "waybill_batch_generated",
        extra={"count": len(contexts), "pdf_size_bytes": len(pdf)},
    )
    return pdf


class WaybillRenderError(RuntimeError):
    """Raised when a waybill cannot be rendered."""


__all__ = [
    "LABEL_SIZE",
    "MAX_ITEMS",
    "TEMPLATE_NAME",
    "WaybillRenderError",
    "build_context",
    "generate_waybill_batch_pdf",
    "generate_waybill_pdf",
    "render_html",
]
