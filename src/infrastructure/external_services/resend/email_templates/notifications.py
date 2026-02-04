"""Order notification email templates.

Covers:
1. Order confirmation  — sent to customer after checkout
2. Shipping update     — sent when order is shipped
3. Delivery confirm    — sent when order is delivered

Supports English (en) and Egyptian Arabic (ar) with RTL layout.
"""

_BASE_STYLE_LTR = """
<style>
  body { margin: 0; padding: 0; font-family: 'Segoe UI', Arial, sans-serif; background: #f4f4f5; color: #1a1a2e; line-height: 1.6; }
  .wrapper { max-width: 600px; margin: 0 auto; background: #ffffff; }
  .header { background: linear-gradient(135deg, #D4AF37 0%, #1034A6 100%); padding: 32px 24px; text-align: center; }
  .header h1 { color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.3px; }
  .header p { color: rgba(255,255,255,0.85); margin: 6px 0 0; font-size: 14px; }
  .badge { display: inline-block; background: rgba(255,255,255,0.2); color: #fff; font-size: 12px; padding: 4px 12px; border-radius: 12px; margin-top: 10px; font-weight: 600; letter-spacing: 0.5px; }
  .body { padding: 32px 24px; }
  .body p { margin: 0 0 16px; font-size: 15px; }
  .highlight-box { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 20px; margin: 20px 0; }
  .highlight-box .label { font-size: 12px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.8px; margin: 0 0 4px; }
  .highlight-box .value { font-size: 20px; font-weight: 700; color: #1034A6; margin: 0; }
  table.items { width: 100%; border-collapse: collapse; margin: 16px 0; }
  table.items th { text-align: left; padding: 10px 12px; background: #f8f9fa; color: #6c757d; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e9ecef; }
  table.items td { padding: 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  table.items td.price { text-align: right; font-weight: 600; color: #1034A6; }
  table.items th.price { text-align: right; }
  .total-row { background: #f8f9fa; }
  .total-row td { font-weight: 700; font-size: 16px; color: #1034A6; padding: 14px 12px; }
  .tracking-card { background: linear-gradient(135deg, #f8f9fa, #e9ecef); border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center; }
  .tracking-card .carrier { font-size: 12px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.8px; margin: 0 0 6px; }
  .tracking-card .number { font-size: 22px; font-weight: 700; color: #1034A6; margin: 0; letter-spacing: 1px; }
  .status-steps { margin: 24px 0; padding: 0; }
  .step { display: flex; align-items: flex-start; margin-bottom: 0; }
  .step-dot { width: 28px; min-width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; margin-right: 12px; flex-shrink: 0; }
  .step-dot.done { background: #28a745; color: #fff; }
  .step-dot.active { background: #D4AF37; color: #fff; }
  .step-dot.pending { background: #e9ecef; color: #adb5bd; }
  .step-line { width: 2px; height: 20px; margin-left: 13px; }
  .step-line.done { background: #28a745; }
  .step-line.pending { background: #e9ecef; }
  .step-text { padding-top: 4px; }
  .step-text .title { font-weight: 600; font-size: 14px; color: #333; margin: 0; }
  .step-text .sub { font-size: 12px; color: #6c757d; margin: 2px 0 0; }
  .btn { display: inline-block; padding: 14px 32px; background: #D4AF37; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 700; font-size: 15px; }
  .btn:hover { background: #c5a030; }
  .btn-outline { display: inline-block; padding: 12px 28px; background: transparent; color: #1034A6; text-decoration: none; border: 2px solid #1034A6; border-radius: 6px; font-weight: 600; font-size: 14px; }
  .divider { height: 1px; background: #e9ecef; margin: 24px 0; }
  .footer { padding: 24px; text-align: center; background: #f8f9fa; }
  .footer p { margin: 0; font-size: 12px; color: #999; }
  .footer a { color: #D4AF37; text-decoration: none; }
  .center { text-align: center; }
</style>
"""

_BASE_STYLE_RTL = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Aref+Ruqaa:wght@400;700&family=Cairo:wght@400;600;700&display=swap');
  body { margin: 0; padding: 0; font-family: 'Cairo', 'Segoe UI', Tahoma, Arial, sans-serif; background: #f4f4f5; color: #1a1a2e; line-height: 1.8; direction: rtl; text-align: right; }
  .wrapper { max-width: 600px; margin: 0 auto; background: #ffffff; }
  .header { background: linear-gradient(135deg, #D4AF37 0%, #1034A6 100%); padding: 32px 24px; text-align: center; }
  .header h1 { color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; }
  .header p { color: rgba(255,255,255,0.85); margin: 6px 0 0; font-size: 14px; }
  .badge { display: inline-block; background: rgba(255,255,255,0.2); color: #fff; font-size: 12px; padding: 4px 12px; border-radius: 12px; margin-top: 10px; font-weight: 600; }
  .brand { font-family: 'Aref Ruqaa', 'Traditional Arabic', serif; font-weight: 700; }
  .body { padding: 32px 24px; }
  .body p { margin: 0 0 16px; font-size: 15px; }
  .highlight-box { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 20px; margin: 20px 0; }
  .highlight-box .label { font-size: 12px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.8px; margin: 0 0 4px; }
  .highlight-box .value { font-size: 20px; font-weight: 700; color: #1034A6; margin: 0; }
  table.items { width: 100%; border-collapse: collapse; margin: 16px 0; }
  table.items th { text-align: right; padding: 10px 12px; background: #f8f9fa; color: #6c757d; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e9ecef; }
  table.items td { padding: 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  table.items td.price { text-align: left; font-weight: 600; color: #1034A6; }
  table.items th.price { text-align: left; }
  .total-row { background: #f8f9fa; }
  .total-row td { font-weight: 700; font-size: 16px; color: #1034A6; padding: 14px 12px; }
  .tracking-card { background: linear-gradient(135deg, #f8f9fa, #e9ecef); border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center; }
  .tracking-card .carrier { font-size: 12px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.8px; margin: 0 0 6px; }
  .tracking-card .number { font-size: 22px; font-weight: 700; color: #1034A6; margin: 0; letter-spacing: 1px; }
  .status-steps { margin: 24px 0; padding: 0; }
  .step { display: flex; align-items: flex-start; margin-bottom: 0; }
  .step-dot { width: 28px; min-width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 700; margin-left: 12px; margin-right: 0; flex-shrink: 0; }
  .step-dot.done { background: #28a745; color: #fff; }
  .step-dot.active { background: #D4AF37; color: #fff; }
  .step-dot.pending { background: #e9ecef; color: #adb5bd; }
  .step-line { width: 2px; height: 20px; margin-right: 13px; margin-left: 0; }
  .step-line.done { background: #28a745; }
  .step-line.pending { background: #e9ecef; }
  .step-text { padding-top: 4px; }
  .step-text .title { font-weight: 600; font-size: 14px; color: #333; margin: 0; }
  .step-text .sub { font-size: 12px; color: #6c757d; margin: 2px 0 0; }
  .btn { display: inline-block; padding: 14px 32px; background: #D4AF37; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 700; font-size: 15px; }
  .btn:hover { background: #c5a030; }
  .btn-outline { display: inline-block; padding: 12px 28px; background: transparent; color: #1034A6; text-decoration: none; border: 2px solid #1034A6; border-radius: 6px; font-weight: 600; font-size: 14px; }
  .divider { height: 1px; background: #e9ecef; margin: 24px 0; }
  .footer { padding: 24px; text-align: center; background: #f8f9fa; }
  .footer p { margin: 0; font-size: 12px; color: #999; }
  .footer a { color: #D4AF37; text-decoration: none; }
  .center { text-align: center; }
</style>
"""

_FOOTER = {
    "en": {
        "help": "Need help? Reply to this email or contact our support team.",
        "copyright": "&copy; 2026 NUMU. All rights reserved.",
    },
    "ar": {
        "help": "محتاج مساعدة؟ رد على الإيميل ده أو تواصل مع فريق الدعم.",
        "copyright": '&copy; 2026 <span class="brand">نُمو</span>. جميع الحقوق محفوظة.',
    },
}


def _wrap(body_html: str, language: str = "en") -> str:
    style = _BASE_STYLE_RTL if language == "ar" else _BASE_STYLE_LTR
    direction = "rtl" if language == "ar" else "ltr"
    f = _FOOTER.get(language, _FOOTER["en"])
    return f"""<!DOCTYPE html>
<html lang="{language}" dir="{direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{style}</head>
<body><div class="wrapper">{body_html}
<div class="footer">
  <p>{f['help']}</p>
  <p style="margin-top:8px;">{f['copyright']}</p>
</div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# 1. Order Confirmation
# ---------------------------------------------------------------------------

_ORDER_CONFIRMATION = {
    "en": {
        "title": "Order Confirmed",
        "subtitle": "Thank you for your purchase!",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "We've received your order and it's being prepared. Here's a summary of what you ordered:",
        "th_item": "Item",
        "th_qty": "Qty",
        "th_price": "Price",
        "total": "Total",
        "order_number_label": "Order Number",
        "what_next": "What happens next?",
        "what_next_body": "The merchant will confirm your order shortly. You'll receive another email once your order ships with tracking details.",
        "btn": "View Order Status",
    },
    "ar": {
        "title": "تم تأكيد الطلب",
        "subtitle": "!شكراً لطلبك",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً،",
        "intro": "استلمنا طلبك وبيتجهّز. دي ملخص اللي طلبته:",
        "th_item": "المنتج",
        "th_qty": "الكمية",
        "th_price": "السعر",
        "total": "المجموع",
        "order_number_label": "رقم الطلب",
        "what_next": "إيه اللي هيحصل بعد كده؟",
        "what_next_body": "التاجر هيأكد طلبك قريب. هتوصلك رسالة تانية لما الطلب يتشحن مع تفاصيل التتبع.",
        "btn": "تتبع حالة الطلب",
    },
}


def order_confirmation_html(
    order_number: str,
    items: list[dict],
    total: float,
    currency: str = "EGP",
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "en",
) -> str:
    """Render order confirmation email."""
    c = _ORDER_CONFIRMATION.get(language, _ORDER_CONFIRMATION["en"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    rows = ""
    for item in items:
        qty = item.get("quantity", 1)
        price = item.get("price", 0)
        rows += f"""
        <tr>
            <td>{item["name"]}</td>
            <td>{qty}</td>
            <td class="price">{currency} {price:,.2f}</td>
        </tr>"""

    return _wrap(f"""
    <div class="header">
        <h1>{c['title']}</h1>
        <p>{c['subtitle']}</p>
        <span class="badge">#{order_number}</span>
    </div>
    <div class="body">
        <p>{greeting}</p>
        <p>{c['intro']}</p>

        <table class="items">
            <thead>
                <tr>
                    <th>{c['th_item']}</th>
                    <th>{c['th_qty']}</th>
                    <th class="price">{c['th_price']}</th>
                </tr>
            </thead>
            <tbody>
                {rows}
                <tr class="total-row">
                    <td colspan="2">{c['total']}</td>
                    <td class="price">{currency} {total:,.2f}</td>
                </tr>
            </tbody>
        </table>

        <div class="highlight-box">
            <p class="label">{c['order_number_label']}</p>
            <p class="value">#{order_number}</p>
        </div>

        <div class="divider"></div>

        <p><strong>{c['what_next']}</strong></p>
        <p>{c['what_next_body']}</p>

        <p class="center" style="margin-top: 24px;">
            <a href="#" class="btn-outline">{c['btn']}</a>
        </p>
    </div>""", language=language)


def _order_confirmation_subject(
    order_number: str, store_name: str = "NUMU", language: str = "en"
) -> str:
    if language == "ar":
        return f"تم تأكيد الطلب #{order_number} — {store_name}"
    return f"Order Confirmed #{order_number} — {store_name}"


ORDER_CONFIRMATION_TEMPLATE = {
    "subject_fn": _order_confirmation_subject,
    "html_fn": order_confirmation_html,
}


# ---------------------------------------------------------------------------
# 2. Shipping Notification
# ---------------------------------------------------------------------------

_SHIPPING = {
    "en": {
        "title": "Your Order is On Its Way!",
        "subtitle": "Sit back — your package is headed to you",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Great news! Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been shipped.",
        "tracking_label": "Tracking Number",
        "step1": "Order Placed",
        "step1_sub": "We received your order",
        "step2": "Order Confirmed",
        "step2_sub": "Merchant confirmed your order",
        "step3": "Shipped",
        "step3_sub": "Your package is on its way",
        "step4": "Delivered",
        "step4_sub": "Arriving soon",
        "btn": "Track Your Package",
    },
    "ar": {
        "title": "!طلبك في الطريق",
        "subtitle": "استنى — الطرد في السكة",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً،",
        "intro": "خبر حلو! طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> اتشحن.",
        "tracking_label": "رقم التتبع",
        "step1": "تم الطلب",
        "step1_sub": "استلمنا طلبك",
        "step2": "تم التأكيد",
        "step2_sub": "التاجر أكّد طلبك",
        "step3": "تم الشحن",
        "step3_sub": "الطرد في الطريق",
        "step4": "تم التسليم",
        "step4_sub": "هيوصل قريب",
        "btn": "تتبع الطرد",
    },
}


def shipping_notification_html(
    order_number: str,
    tracking_number: str | None = None,
    carrier: str | None = None,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "en",
) -> str:
    c = _SHIPPING.get(language, _SHIPPING["en"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    tracking_section = ""
    if tracking_number:
        carrier_label = carrier or ("شركة الشحن" if language == "ar" else "Carrier")
        tracking_section = f"""
        <div class="tracking-card">
            <p class="carrier">{carrier_label} {c['tracking_label']}</p>
            <p class="number">{tracking_number}</p>
        </div>"""

    return _wrap(f"""
    <div class="header">
        <h1>{c['title']}</h1>
        <p>{c['subtitle']}</p>
        <span class="badge">#{order_number}</span>
    </div>
    <div class="body">
        <p>{greeting}</p>
        <p>{c['intro'].format(order_number=order_number, store_name=store_name)}</p>

        {tracking_section}

        <div class="status-steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step1']}</p>
                    <p class="sub">{c['step1_sub']}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step2']}</p>
                    <p class="sub">{c['step2_sub']}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot active">&#10148;</div>
                <div class="step-text">
                    <p class="title">{c['step3']}</p>
                    <p class="sub">{c['step3_sub']}</p>
                </div>
            </div>
            <div class="step-line pending"></div>
            <div class="step">
                <div class="step-dot pending">4</div>
                <div class="step-text">
                    <p class="title">{c['step4']}</p>
                    <p class="sub">{c['step4_sub']}</p>
                </div>
            </div>
        </div>

        <div class="divider"></div>

        <p class="center" style="margin-top: 16px;">
            <a href="#" class="btn">{c['btn']}</a>
        </p>
    </div>""", language=language)


def _shipping_subject(
    order_number: str, store_name: str = "NUMU", language: str = "en"
) -> str:
    if language == "ar":
        return f"طلبك #{order_number} اتشحن! — {store_name}"
    return f"Your Order #{order_number} Has Shipped! — {store_name}"


SHIPPING_NOTIFICATION_TEMPLATE = {
    "subject_fn": _shipping_subject,
    "html_fn": shipping_notification_html,
}


# ---------------------------------------------------------------------------
# 3. Delivery Confirmation
# ---------------------------------------------------------------------------

_DELIVERY = {
    "en": {
        "title": "Your Order Has Been Delivered!",
        "subtitle": "We hope you love it",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been delivered successfully.",
        "step1": "Order Placed",
        "step2": "Order Confirmed",
        "step3": "Shipped",
        "step4": "Delivered",
        "step4_sub": "Package delivered successfully",
        "complete": "Order Complete",
        "enjoy": "We hope you enjoy your purchase! If anything isn't right, please don't hesitate to reach out.",
        "thanks": "Thank you for shopping with <strong>{store_name}</strong>.",
    },
    "ar": {
        "title": "!تم تسليم طلبك",
        "subtitle": "يا رب يعجبك",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً،",
        "intro": "طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> اتسلّم بنجاح.",
        "step1": "تم الطلب",
        "step2": "تم التأكيد",
        "step3": "تم الشحن",
        "step4": "تم التسليم",
        "step4_sub": "الطرد اتسلّم بنجاح",
        "complete": "الطلب مكتمل",
        "enjoy": "نتمنى يعجبك! لو في أي حاجة مش مظبوطة، تواصل معانا.",
        "thanks": "شكراً إنك اشتريت من <strong>{store_name}</strong>.",
    },
}


def delivery_confirmation_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "en",
) -> str:
    c = _DELIVERY.get(language, _DELIVERY["en"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    return _wrap(f"""
    <div class="header">
        <h1>{c['title']}</h1>
        <p>{c['subtitle']}</p>
        <span class="badge">#{order_number}</span>
    </div>
    <div class="body">
        <p>{greeting}</p>
        <p>{c['intro'].format(order_number=order_number, store_name=store_name)}</p>

        <div class="status-steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step1']}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step2']}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step3']}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c['step4']}</p>
                    <p class="sub">{c['step4_sub']}</p>
                </div>
            </div>
        </div>

        <div class="highlight-box" style="text-align: center;">
            <p style="font-size: 32px; margin: 0;">&#127881;</p>
            <p style="font-size: 16px; font-weight: 600; color: #28a745; margin: 8px 0 0;">{c['complete']}</p>
        </div>

        <div class="divider"></div>

        <p>{c['enjoy']}</p>

        <p>{c['thanks'].format(store_name=store_name)}</p>
    </div>""", language=language)


def _delivery_subject(
    order_number: str, store_name: str = "NUMU", language: str = "en"
) -> str:
    if language == "ar":
        return f"الطلب #{order_number} اتسلّم — {store_name}"
    return f"Order #{order_number} Delivered — {store_name}"


DELIVERY_CONFIRMATION_TEMPLATE = {
    "subject_fn": _delivery_subject,
    "html_fn": delivery_confirmation_html,
}
