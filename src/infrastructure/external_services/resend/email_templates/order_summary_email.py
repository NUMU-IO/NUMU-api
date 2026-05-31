"""Rich "new order" email — shared by the customer confirmation and the
merchant new-order alert.

Layout (matches the reference design):
  • order-creation date line (store-local timezone)
  • greeting + intro (audience-aware: customer vs merchant)
  • "حالة الطلب: جديد" status badge
  • package graphic
  • horizontal 3-step tracker (طلب جديد → جاري التوصيل → تم الاستلام)
  • products table (thumbnail / "no image", name, qty, price incl. tax)
  • order summary (قيمة المنتجات + optional shipping/total)
  • CTA button (customer → track order, merchant → manage in dashboard)

Brand chrome (header hero, fonts, footer, dark-mode) comes from `_base`.
Egyptian Arabic ("ar") is the default; English ("en") is the fallback.
All money inputs are in CENTS.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.infrastructure.external_services.resend.email_templates._base import (
    GOLD,
    HAIRLINE,
    MUTED,
    NAVY,
    SUCCESS,
    header,
    wrap,
)

# ── Copy bank ─────────────────────────────────────────────────────────────
_COPY = {
    "ar": {
        "customer_title": "تم تأكيد طلبك",
        "customer_subtitle": "شكراً إنك اشتريت من عندنا",
        "customer_intro": (
            "تم تأكيد طلبك ويسعدنا إبلاغك بأننا نقوم الآن بتجهيزه، "
            "سنبقيك على إطلاع بحالة طلبك."
        ),
        "merchant_title": "طلب جديد",
        "merchant_subtitle": "وصلك طلب جديد",
        "merchant_intro": "مبروك! وصلك طلب جديد على متجرك <strong>{store_name}</strong>. دي تفاصيله:",
        "greeting": "اهلاً {name} 👋",
        "greeting_default": "اهلاً 👋",
        "date_label": "تاريخ إنشاء الطلب",
        "status_label": "حالة الطلب",
        "status_new": "جديد",
        "steps": ["طلب جديد", "جاري التوصيل", "تم الاستلام"],
        "products_heading": "المنتجات",
        "th_product": "المنتجات",
        "th_qty": "الكمية",
        "th_price": "السعر شامل الضريبة",
        "no_image": "لا توجد صورة",
        "summary_heading": "ملخص الطلب",
        "products_value": "قيمة المنتجات",
        "shipping": "الشحن",
        "total": "الإجمالي",
        "btn_customer": "متابعة حالة الطلب",
        "btn_merchant": "إدارة الطلب في لوحة التحكم",
        "preheader_customer": "تم تأكيد طلبك على نُمو",
        "preheader_merchant": "طلب جديد على متجرك",
        "ampm": ("ص", "م"),
    },
    "en": {
        "customer_title": "Order Confirmed",
        "customer_subtitle": "Thank you for your purchase",
        "customer_intro": (
            "Your order is confirmed and we're preparing it now — "
            "we'll keep you posted on its status."
        ),
        "merchant_title": "New Order",
        "merchant_subtitle": "You've got a new order",
        "merchant_intro": "Congrats! You've got a new order on <strong>{store_name}</strong>. Here are the details:",
        "greeting": "Hi {name} 👋",
        "greeting_default": "Hi 👋",
        "date_label": "Order date",
        "status_label": "Order status",
        "status_new": "New",
        "steps": ["New order", "On the way", "Received"],
        "products_heading": "Products",
        "th_product": "Products",
        "th_qty": "Qty",
        "th_price": "Price incl. tax",
        "no_image": "no image",
        "summary_heading": "Order summary",
        "products_value": "Products value",
        "shipping": "Shipping",
        "total": "Total",
        "btn_customer": "Track your order",
        "btn_merchant": "Manage order in dashboard",
        "preheader_customer": "Your NUMU order is confirmed",
        "preheader_merchant": "New order on your store",
        "ampm": ("AM", "PM"),
    },
}


def _money(cents: int | float, currency: str) -> str:
    """Format a cents amount as ``CCY 0,000.00`` (latin digits, matches ref)."""
    return f"{currency} {(cents or 0) / 100:,.2f}"


def _format_date_line(
    dt: datetime, tz_name: str, language: str, label: str, ampm: tuple[str, str]
) -> str:
    """Render ``<label> | DD-MM-YYYY | hh:mm <ampm> GMT±HH:MM`` in store tz."""
    try:
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt
    date_str = local.strftime("%d-%m-%Y")
    h12 = local.strftime("%I:%M").lstrip("0") or "12:00"
    is_pm = local.strftime("%p") == "PM"
    mer = ampm[1] if is_pm else ampm[0]
    off = local.utcoffset()
    if off is not None:
        total_min = int(off.total_seconds() // 60)
        sign = "+" if total_min >= 0 else "-"
        hh, mm = divmod(abs(total_min), 60)
        gmt = f"GMT{sign}{hh:02d}:{mm:02d}"
    else:
        gmt = "GMT+00:00"
    return f"{label} | {date_str} | {h12} {mer} {gmt}"


def _stepper(steps: list[str], active_index: int) -> str:
    """Horizontal 3-step tracker as an email-safe table. Active = gold/navy
    circle; later steps = muted. RTL puts step 1 on the right automatically."""
    cols = ""
    width = f"{100 // max(len(steps), 1)}%"
    for i, label in enumerate(steps):
        n = i + 1
        if i == active_index:
            circle = (
                f"background:{SUCCESS};color:#ffffff;"
                f"box-shadow:0 0 0 4px rgba(31,138,76,0.18);"
            )
            text_color = NAVY
            text_weight = "700"
        else:
            circle = f"background:{HAIRLINE};color:{MUTED};"
            text_color = MUTED
            text_weight = "600"
        cols += (
            f'<td width="{width}" align="center" valign="top" '
            f'style="padding:4px 2px;">'
            f'<div style="width:34px;height:34px;line-height:34px;border-radius:50%;'
            f"display:inline-block;text-align:center;font-size:14px;font-weight:700;"
            f'font-family:Inter,Arial,sans-serif;{circle}">{n}</div>'
            f'<div style="margin-top:8px;font-size:12px;font-weight:{text_weight};'
            f'color:{text_color};">{label}</div>'
            f"</td>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin:20px 0 4px;"><tr>{cols}</tr></table>'
    )


def _tracking_illustration_url() -> str:
    """Absolute URL of the order-tracking illustration (hosted PNG so it
    renders in email clients that strip SVG/data-URIs, e.g. Gmail). Reads the
    brand-assets base so dev/staging/prod can override the host."""
    try:
        from src.config import settings

        base = settings.brand_assets_base_url.rstrip("/")
    except Exception:
        base = "https://numueg.app"
    return f"{base}/email/order-tracking.png"


def _package_graphic() -> str:
    """Centered order-tracking illustration (hosted PNG)."""
    url = _tracking_illustration_url()
    return (
        '<div style="text-align:center;margin:12px 0 6px;">'
        f'<img src="{url}" alt="" width="200" '
        'style="width:200px;max-width:62%;height:auto;border:0;">'
        "</div>"
    )


def _items_table(items: list[dict], currency: str, c: dict, language: str) -> str:
    """Products table: thumbnail / 'no image', name, qty, price incl. tax."""
    rows = ""
    for it in items:
        name = it.get("name") or ""
        qty = it.get("quantity", 1)
        total_cents = it.get("total_cents", it.get("unit_price_cents", 0) * qty)
        image_url = it.get("image_url")
        if image_url:
            thumb = (
                f'<img src="{image_url}" alt="" width="48" height="48" '
                'style="width:48px;height:48px;border-radius:8px;object-fit:cover;'
                f'border:1px solid {HAIRLINE};vertical-align:middle;">'
            )
        else:
            thumb = (
                f'<span style="display:inline-block;width:48px;height:48px;'
                f"border-radius:8px;background:{HAIRLINE};color:{MUTED};"
                "font-size:9px;line-height:48px;text-align:center;"
                f'vertical-align:middle;">{c["no_image"]}</span>'
            )
        rows += (
            "<tr>"
            f'<td style="padding:12px 6px;border-bottom:1px solid {HAIRLINE};">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="padding:0;">{thumb}</td>'
            f'<td style="padding:0 10px;font-size:14px;color:{NAVY};font-weight:600;">{name}</td>'
            "</tr></table></td>"
            f'<td align="center" style="padding:12px 6px;border-bottom:1px solid {HAIRLINE};'
            f'font-size:14px;color:{NAVY};">{qty}</td>'
            f'<td align="center" style="padding:12px 6px;border-bottom:1px solid {HAIRLINE};'
            f'font-size:14px;color:{NAVY};font-weight:700;direction:ltr;">'
            f"{_money(total_cents, currency)}</td>"
            "</tr>"
        )
    return (
        f'<h2 style="font-size:17px;color:{NAVY};margin:26px 0 10px;font-weight:700;">'
        f"{c['products_heading']}</h2>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "<thead><tr>"
        f'<th align="{"right" if language == "ar" else "left"}" '
        f'style="padding:10px 6px;font-size:11px;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:0.6px;border-bottom:2px solid {HAIRLINE};">{c["th_product"]}</th>'
        f'<th align="center" style="padding:10px 6px;font-size:11px;color:{MUTED};'
        f'text-transform:uppercase;letter-spacing:0.6px;border-bottom:2px solid {HAIRLINE};">'
        f"{c['th_qty']}</th>"
        f'<th align="center" style="padding:10px 6px;font-size:11px;color:{MUTED};'
        f'text-transform:uppercase;letter-spacing:0.6px;border-bottom:2px solid {HAIRLINE};">'
        f"{c['th_price']}</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _summary(
    products_value_cents: int,
    shipping_cents: int | None,
    total_cents: int | None,
    currency: str,
    c: dict,
) -> str:
    """Order summary block: products value (+ shipping + total when present)."""

    def row(label: str, value_cents: int, bold: bool = False) -> str:
        weight = "700" if bold else "500"
        size = "16px" if bold else "14px"
        return (
            "<tr>"
            f'<td style="padding:8px 0;font-size:{size};color:{NAVY};font-weight:{weight};">{label}</td>'
            f'<td align="left" style="padding:8px 0;font-size:{size};color:{NAVY};'
            f'font-weight:700;direction:ltr;">{_money(value_cents, currency)}</td>'
            "</tr>"
        )

    body = row(c["products_value"], products_value_cents)
    if shipping_cents:
        body += row(c["shipping"], shipping_cents)
    if total_cents is not None and total_cents != products_value_cents:
        body += (
            f'<tr><td colspan="2" style="border-top:1px solid {HAIRLINE};padding:0;"></td></tr>'
            + row(c["total"], total_cents, bold=True)
        )
    return (
        f'<h2 style="font-size:17px;color:{NAVY};margin:26px 0 6px;font-weight:700;">'
        f"{c['summary_heading']}</h2>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{body}</table>'
    )


def new_order_email_html(
    *,
    audience: str,
    order_number: str,
    items: list[dict],
    products_value_cents: int,
    currency: str = "EGP",
    store_name: str = "NUMU",
    recipient_name: str | None = None,
    created_at: datetime | None = None,
    timezone_name: str = "Africa/Cairo",
    order_url: str | None = None,
    shipping_cents: int | None = None,
    total_cents: int | None = None,
    instapay: dict | None = None,
    language: str = "ar",
) -> str:
    """Render the shared rich "new order" email for ``customer`` or ``merchant``."""
    lang = language if language in _COPY else "ar"
    c = _COPY[lang]
    is_merchant = audience == "merchant"

    # InstaPay manual-payment instructions (customer only) — preserved from
    # the legacy confirmation so a customer who closed the tab still has the
    # IPA / reference / amount / expiry to complete payment.
    instapay_block = ""
    if instapay and not is_merchant:
        from src.infrastructure.external_services.resend.email_templates.instapay import (
            instapay_instructions_html,
        )

        instapay_block = instapay_instructions_html(
            ipa=instapay.get("ipa", ""),
            reference_code=instapay.get("reference_code", ""),
            amount_cents=int(instapay.get("amount_cents", 0)),
            currency=instapay.get("currency", currency),
            expires_at=instapay.get("expires_at"),
            resume_url=instapay.get("resume_url"),
            fallback_phone=instapay.get("fallback_phone"),
            language=lang,
        )

    title = c["merchant_title"] if is_merchant else c["customer_title"]
    subtitle = c["merchant_subtitle"] if is_merchant else c["customer_subtitle"]
    greeting = (
        c["greeting"].format(name=recipient_name)
        if recipient_name
        else c["greeting_default"]
    )
    intro = (
        c["merchant_intro"].format(store_name=store_name)
        if is_merchant
        else c["customer_intro"]
    )

    date_line = ""
    if created_at is not None:
        date_line = (
            f'<p style="font-size:12px;color:{MUTED};margin:0 0 18px;">'
            f"{_format_date_line(created_at, timezone_name, lang, c['date_label'], c['ampm'])}"
            "</p>"
        )

    status_badge = (
        f'<p style="font-size:15px;font-weight:700;color:{NAVY};margin:18px 0 0;">'
        f'{c["status_label"]}: <span style="color:{SUCCESS};">{c["status_new"]}</span></p>'
    )

    btn_label = c["btn_merchant"] if is_merchant else c["btn_customer"]
    button = (
        '<p style="text-align:center;margin:30px 0 8px;">'
        f'<a href="{order_url}" style="display:inline-block;padding:14px 34px;'
        f"background:{GOLD};color:#ffffff;text-decoration:none;border-radius:8px;"
        f'font-weight:700;font-size:15px;">{btn_label}</a></p>'
        if order_url
        else ""
    )

    body = f"""
    {header(title, subtitle, badge=f"#{order_number}", language=lang)}
    <div class="body">
        {date_line}
        <p class="lead">{greeting}</p>
        <p>{intro}</p>
        {status_badge}
        {_package_graphic()}
        {_stepper(c["steps"], 0)}
        {_items_table(items, currency, c, lang)}
        {_summary(products_value_cents, shipping_cents, total_cents, currency, c)}
        {instapay_block}
        {button}
    </div>"""

    preheader = c["preheader_merchant"] if is_merchant else c["preheader_customer"]
    return wrap(body, language=lang, preheader=preheader)
