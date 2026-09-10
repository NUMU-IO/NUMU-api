"""Cart-recovery email — the branded version.

The recovery send used to build its own HTML inline in the route:

    <p>Hi there,</p>
    <p>You left items in your cart at <strong>vionne</strong>.</p>
    <ul><li>Elegance - Burgundy × 1</li></ul>
    <p>Come back and finish your order whenever you're ready.</p>

Unstyled, addressed to nobody, no pictures, no prices, and — the part that
made it pointless — **no link back to the cart**. It asked a shopper to come
back and then made them find their own way there.

Everything it needed was already stored. The cart snapshot carries
``product_name``, ``variant_name``, ``quantity``, ``unit_price``,
``total_price`` and ``image_url`` per line, plus the totals; the shopper's
name is on the checkout's shipping address when they typed one. This renders
all of it through the same chrome every other NUMU email uses, and the items
table is the one from the order-summary template rather than a second copy.
"""

from __future__ import annotations

from src.infrastructure.external_services.resend.email_templates._base import (
    GOLD,
    HAIRLINE,
    MUTED,
    NAVY,
    header,
    wrap,
)
from src.infrastructure.external_services.resend.email_templates.order_summary_email import (  # noqa: E501
    _items_table,
    _money,
)

_COPY = {
    "ar": {
        "title": "سلتك لسه مستنياك",
        "subtitle": "المنتجات محجوزة في سلتك",
        "greeting": "اهلاً {name} 👋",
        "greeting_default": "اهلاً 👋",
        "intro": "سبت المنتجات دي في سلتك عند <strong>{store_name}</strong>، وهي لسه موجودة.",
        "products_heading": "اللي في سلتك",
        "th_product": "المنتجات",
        "th_qty": "الكمية",
        "th_price": "السعر",
        "no_image": "لا توجد صورة",
        "total_label": "الإجمالي",
        "cta": "كمّل طلبك",
        "reassure": "الرابط ده بيرجّعلك السلة بنفس المنتجات — مش هتحتاج تدوّر عليها تاني.",
        "preheader": "منتجاتك لسه في السلة — كمّل طلبك",
    },
    "en": {
        "title": "Your cart is still waiting",
        "subtitle": "We saved what you picked",
        "greeting": "Hi {name} 👋",
        "greeting_default": "Hi 👋",
        "intro": "You left these items in your cart at <strong>{store_name}</strong>, and they're still there.",
        "products_heading": "In your cart",
        "th_product": "Product",
        "th_qty": "Qty",
        "th_price": "Price",
        "no_image": "No image",
        "total_label": "Total",
        "cta": "Complete your order",
        "reassure": "This link puts everything back in your cart — you won't have to find it again.",
        "preheader": "Your items are still in your cart",
    },
}


def _normalise(line_items: list[dict]) -> list[dict]:
    """Cart lines into the shape the shared items table renders.

    The cart snapshot and the order line item name the same things
    differently (`product_name`/`total_price` vs `name`/`total_cents`), which
    is the only reason this function exists.
    """
    out: list[dict] = []
    for li in line_items or []:
        quantity = int(li.get("quantity") or 1)
        unit = int(li.get("unit_price") or 0)
        out.append({
            "name": li.get("product_name") or li.get("name") or "—",
            "variant_name": li.get("variant_name") or "",
            "quantity": quantity,
            "total_cents": int(li.get("total_price") or unit * quantity),
            "image_url": li.get("image_url"),
        })
    return out


def abandoned_cart_email_html(
    *,
    store_name: str,
    recovery_url: str,
    line_items: list[dict],
    total_cents: int,
    currency: str = "EGP",
    customer_name: str | None = None,
    language: str = "ar",
    logo_url: str | None = None,
) -> tuple[str, str]:
    """Return ``(subject, html)`` for a cart-recovery email."""
    lang = "ar" if language == "ar" else "en"
    c = _COPY[lang]

    greeting = (
        c["greeting"].format(name=customer_name)
        if customer_name
        else c["greeting_default"]
    )
    items = _normalise(line_items)

    total_row = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:14px 0 0;">'
        f'<tr><td style="padding:12px 6px;font-size:15px;color:{NAVY};font-weight:700;'
        f'border-top:2px solid {HAIRLINE};">{c["total_label"]}</td>'
        f'<td align="{"left" if lang == "ar" else "right"}" '
        f'style="padding:12px 6px;font-size:15px;color:{NAVY};font-weight:700;'
        f'direction:ltr;border-top:2px solid {HAIRLINE};">'
        f"{_money(total_cents, currency)}</td></tr></table>"
    )

    # The whole point of the email. Centred, gold, and repeated nowhere else —
    # one obvious way back to the cart.
    button = (
        '<p style="text-align:center;margin:28px 0 10px;">'
        f'<a href="{recovery_url}" style="display:inline-block;padding:14px 34px;'
        f"background:{GOLD};color:#ffffff;text-decoration:none;border-radius:8px;"
        f'font-weight:700;font-size:15px;">{c["cta"]}</a></p>'
        f'<p style="text-align:center;margin:0;font-size:12px;color:{MUTED};">'
        f"{c['reassure']}</p>"
    )

    body = f"""
    {header(c["title"], c["subtitle"], language=lang, brand_name=store_name, logo_url=logo_url)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(store_name=store_name)}</p>
        {_items_table(items, currency, c, lang)}
        {total_row}
        {button}
    </div>"""

    subject = (
        f"سلتك في {store_name} لسه مستنياك"
        if lang == "ar"
        else f"You left items in your cart at {store_name}"
    )
    return subject, wrap(
        body, language=lang, preheader=c["preheader"], brand_name=store_name
    )
