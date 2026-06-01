"""Merchant-facing transactional email templates.

Distinct from `notifications.py` (which targets *customers*). These emails
go to the store owner / merchant. Currently:

- ``merchant_new_order_*`` — a "you got a new order" alert sent to the
  store owner the moment a customer places an order, so the merchant can
  jump into the dashboard and start fulfilling.

Egyptian Arabic ("ar") is the default to match the rest of the NUMU brand
emails; English ("en") is provided as a secondary fallback.
"""

from __future__ import annotations

from src.infrastructure.external_services.resend.email_templates._base import (
    header,
    wrap,
)


def merchant_new_order_subject(
    order_number: str, store_name: str, *, language: str = "ar"
) -> str:
    """Build the subject line for the new-order merchant alert."""
    if language == "ar":
        return f"طلب جديد #{order_number} على {store_name} 🎉"
    return f"New order #{order_number} on {store_name} 🎉"


def merchant_new_order_html(
    *,
    order_number: str,
    store_name: str,
    total_cents: float,
    currency: str = "EGP",
    customer_name: str | None = None,
    order_url: str | None = None,
    language: str = "ar",
    logo_url: str | None = None,
) -> str:
    """Render the new-order merchant alert body.

    ``total_cents`` is the order total in minor units (cents) — matches
    ``OrderCreatedEvent.total`` — and is divided by 100 for display.
    """
    is_ar = language == "ar"
    amount = f"{(total_cents or 0) / 100:,.2f}"

    if is_ar:
        title = "طلب جديد 🎉"
        lead = "وصلك طلب جديد!"
        intro = (
            f"عميل عمل طلب جديد على متجرك <strong>{store_name}</strong>. "
            "دي تفاصيله بسرعة:"
        )
        lbl_order = "رقم الطلب"
        lbl_total = "الإجمالي"
        lbl_customer = "العميل"
        cta = "افتح الطلب في لوحة التحكم"
        outro = "ادخل لوحة التحكم عشان تأكّد الطلب وتجهّزه للشحن."
        preheader = f"طلب جديد بقيمة {amount} {currency} على {store_name}"
    else:
        title = "New order 🎉"
        lead = "You've got a new order!"
        intro = (
            f"A customer just placed an order on your store "
            f"<strong>{store_name}</strong>. Here's a quick summary:"
        )
        lbl_order = "Order number"
        lbl_total = "Total"
        lbl_customer = "Customer"
        cta = "Open order in dashboard"
        outro = "Head to your dashboard to confirm the order and prep it for shipping."
        preheader = f"New order worth {amount} {currency} on {store_name}"

    customer_block = (
        f'<hr class="divider" style="margin:16px 0;">'
        f'<p class="label">{lbl_customer}</p>'
        f'<p class="value" style="font-size:16px;">{customer_name}</p>'
        if customer_name
        else ""
    )

    button = (
        f'<p class="center" style="margin:28px 0;">'
        f'<a href="{order_url}" class="btn">{cta}</a></p>'
        if order_url
        else ""
    )

    body = f"""
    {header(title, language=language, brand_name=store_name, logo_url=logo_url)}
    <div class="body">
        <p class="lead">{lead}</p>
        <p>{intro}</p>

        <div class="panel">
            <p class="label">{lbl_order}</p>
            <p class="value">#{order_number}</p>
            <hr class="divider" style="margin:16px 0;">
            <p class="label">{lbl_total}</p>
            <p class="value">{amount} {currency}</p>
            {customer_block}
        </div>

        {button}

        <p class="muted" style="margin-top:24px;">{outro}</p>
    </div>"""

    return wrap(body, language=language, preheader=preheader, brand_name=store_name)
