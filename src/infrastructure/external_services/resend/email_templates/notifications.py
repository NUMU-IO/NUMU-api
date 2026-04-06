"""Order notification email templates.

Covers customer-facing order lifecycle emails:
1. Order confirmation
2. Order confirmed (merchant accepted)
3. Order processing
4. Shipping notification
5. Delivery confirmation
6. Order cancelled
7. Order refunded

Egyptian Arabic ("ar") is the primary/default language. RTL layout, Cairo
typography, and the shared NUMU brand chrome come from `_base`.
"""

from src.infrastructure.external_services.resend.email_templates._base import (
    header,
    wrap,
)

# ─────────────────────────────────────────────────────────────────────────
# 1. Order Confirmation
# ─────────────────────────────────────────────────────────────────────────

_ORDER_CONFIRMATION = {
    "ar": {
        "title": "تم تأكيد طلبك",
        "subtitle": "شكراً إنك اشتريت من عندنا",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "استلمنا طلبك وبيتجهّز. دي تفاصيل اللي طلبته:",
        "th_item": "المنتج",
        "th_qty": "الكمية",
        "th_price": "السعر",
        "total": "الإجمالي",
        "order_number_label": "رقم الطلب",
        "what_next": "إيه اللي هيحصل بعد كده؟",
        "what_next_body": "التاجر هيأكد طلبك في أقرب وقت. هتوصلك رسالة تانية أول ما الطلب يتشحن مع رقم التتبع.",
        "btn": "متابعة حالة الطلب",
        "preheader": "استلمنا طلبك على نُمو",
    },
    "en": {
        "title": "Order Confirmed",
        "subtitle": "Thank you for your purchase",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "We've received your order and it's being prepared. Here's a summary:",
        "th_item": "Item",
        "th_qty": "Qty",
        "th_price": "Price",
        "total": "Total",
        "order_number_label": "Order Number",
        "what_next": "What happens next?",
        "what_next_body": "The merchant will confirm your order shortly. You'll receive another email once your order ships with tracking details.",
        "btn": "View Order Status",
        "preheader": "Your NUMU order is confirmed",
    },
}


def order_confirmation_html(
    order_number: str,
    items: list[dict],
    total: float,
    currency: str = "EGP",
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _ORDER_CONFIRMATION.get(language, _ORDER_CONFIRMATION["ar"])
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

    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"]}</p>

        <table class="items">
            <thead>
                <tr>
                    <th>{c["th_item"]}</th>
                    <th>{c["th_qty"]}</th>
                    <th class="price">{c["th_price"]}</th>
                </tr>
            </thead>
            <tbody>
                {rows}
                <tr class="total">
                    <td colspan="2">{c["total"]}</td>
                    <td class="price">{currency} {total:,.2f}</td>
                </tr>
            </tbody>
        </table>

        <div class="panel">
            <p class="label">{c["order_number_label"]}</p>
            <p class="value">#{order_number}</p>
        </div>

        <hr class="divider">

        <p><strong>{c["what_next"]}</strong></p>
        <p>{c["what_next_body"]}</p>

        <p class="center" style="margin-top:28px;">
            <a href="#" class="btn-outline">{c["btn"]}</a>
        </p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def _order_confirmation_subject(
    order_number: str, store_name: str = "NUMU", language: str = "ar"
) -> str:
    if language == "en":
        return f"Order Confirmed #{order_number} — {store_name}"
    return f"تم تأكيد طلبك #{order_number} — {store_name}"


ORDER_CONFIRMATION_TEMPLATE = {
    "subject_fn": _order_confirmation_subject,
    "html_fn": order_confirmation_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 2. Shipping Notification
# ─────────────────────────────────────────────────────────────────────────

_SHIPPING = {
    "ar": {
        "title": "طلبك في الطريق",
        "subtitle": "خلّي بالك — الطرد جاي ليك",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "خبر حلو! طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> اتشحن.",
        "tracking_label": "رقم التتبع",
        "carrier_default": "شركة الشحن",
        "step1": "تم استلام الطلب",
        "step1_sub": "وصلنا طلبك",
        "step2": "تم التأكيد",
        "step2_sub": "التاجر أكّد الطلب",
        "step3": "تم الشحن",
        "step3_sub": "الطرد بقى في السكة",
        "step4": "تم التسليم",
        "step4_sub": "هيوصلك قريب",
        "btn": "تتبع الطرد",
        "preheader": "طلبك من نُمو اتشحن",
    },
    "en": {
        "title": "Your Order is On Its Way",
        "subtitle": "Sit back — your package is headed your way",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Great news! Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been shipped.",
        "tracking_label": "Tracking Number",
        "carrier_default": "Carrier",
        "step1": "Order Placed",
        "step1_sub": "We received your order",
        "step2": "Order Confirmed",
        "step2_sub": "Merchant confirmed your order",
        "step3": "Shipped",
        "step3_sub": "Your package is on its way",
        "step4": "Delivered",
        "step4_sub": "Arriving soon",
        "btn": "Track Your Package",
        "preheader": "Your NUMU order has shipped",
    },
}


def shipping_notification_html(
    order_number: str,
    tracking_number: str | None = None,
    carrier: str | None = None,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _SHIPPING.get(language, _SHIPPING["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    tracking_section = ""
    if tracking_number:
        carrier_label = carrier or c["carrier_default"]
        tracking_section = f"""
        <div class="panel" style="text-align:center;">
            <p class="label">{carrier_label} • {c["tracking_label"]}</p>
            <p class="value" style="direction:ltr;letter-spacing:1px;">{tracking_number}</p>
        </div>"""

    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>

        {tracking_section}

        <div class="steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c["step1"]}</p>
                    <p class="sub">{c["step1_sub"]}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c["step2"]}</p>
                    <p class="sub">{c["step2_sub"]}</p>
                </div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot active">&#10148;</div>
                <div class="step-text">
                    <p class="title">{c["step3"]}</p>
                    <p class="sub">{c["step3_sub"]}</p>
                </div>
            </div>
            <div class="step-line pending"></div>
            <div class="step">
                <div class="step-dot pending">4</div>
                <div class="step-text">
                    <p class="title">{c["step4"]}</p>
                    <p class="sub">{c["step4_sub"]}</p>
                </div>
            </div>
        </div>

        <hr class="divider">

        <p class="center" style="margin-top:18px;">
            <a href="#" class="btn">{c["btn"]}</a>
        </p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def _shipping_subject(
    order_number: str, store_name: str = "NUMU", language: str = "ar"
) -> str:
    if language == "en":
        return f"Your Order #{order_number} Has Shipped — {store_name}"
    return f"طلبك #{order_number} اتشحن — {store_name}"


SHIPPING_NOTIFICATION_TEMPLATE = {
    "subject_fn": _shipping_subject,
    "html_fn": shipping_notification_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 3. Delivery Confirmation
# ─────────────────────────────────────────────────────────────────────────

_DELIVERY = {
    "ar": {
        "title": "تم تسليم طلبك",
        "subtitle": "يا رب يعجبك",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> اتسلّم بنجاح.",
        "step1": "تم الطلب",
        "step2": "تم التأكيد",
        "step3": "تم الشحن",
        "step4": "تم التسليم",
        "step4_sub": "الطرد وصل بنجاح",
        "complete": "الطلب مكتمل",
        "enjoy": "نتمنى يعجبك. لو في أي حاجة مش مظبوطة، تواصل معانا في أي وقت.",
        "thanks": "شكراً إنك اشتريت من <strong>{store_name}</strong>.",
        "preheader": "طلبك من نُمو وصل",
    },
    "en": {
        "title": "Your Order Has Been Delivered",
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
        "enjoy": "We hope you enjoy your purchase. If anything isn't right, please reach out.",
        "thanks": "Thank you for shopping with <strong>{store_name}</strong>.",
        "preheader": "Your NUMU order arrived",
    },
}


def delivery_confirmation_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _DELIVERY.get(language, _DELIVERY["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>

        <div class="steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step1"]}</p></div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step2"]}</p></div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step3"]}</p></div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text">
                    <p class="title">{c["step4"]}</p>
                    <p class="sub">{c["step4_sub"]}</p>
                </div>
            </div>
        </div>

        <div class="panel" style="text-align:center;">
            <p style="font-size:34px; margin:0;">&#127881;</p>
            <p style="font-size:16px; font-weight:700; color:#1F8A4C; margin:8px 0 0;">{c["complete"]}</p>
        </div>

        <hr class="divider">

        <p>{c["enjoy"]}</p>
        <p>{c["thanks"].format(store_name=store_name)}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def _delivery_subject(
    order_number: str, store_name: str = "NUMU", language: str = "ar"
) -> str:
    if language == "en":
        return f"Order #{order_number} Delivered — {store_name}"
    return f"الطلب #{order_number} اتسلّم — {store_name}"


DELIVERY_CONFIRMATION_TEMPLATE = {
    "subject_fn": _delivery_subject,
    "html_fn": delivery_confirmation_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 4. Order Confirmed (merchant accepted pending order)
# ─────────────────────────────────────────────────────────────────────────

_CONFIRMED = {
    "ar": {
        "title": "تم تأكيد طلبك",
        "subtitle": "بنجهّزهولك",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "خبر حلو! طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> اتأكّد وبيتجهّز دلوقتي.",
        "what_next": "إيه اللي هيحصل بعد كده؟",
        "what_next_body": "التاجر هيبدأ يجهّز طلبك. هتوصلك رسالة تانية أول ما الطلب يتشحن مع رقم التتبع.",
        "preheader": "تاجرك أكّد الطلب",
    },
    "en": {
        "title": "Your Order Has Been Confirmed",
        "subtitle": "We're getting it ready for you",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Great news! Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been confirmed and is being prepared.",
        "what_next": "What happens next?",
        "what_next_body": "The merchant will begin processing your order. You'll receive another email once it ships with tracking details.",
        "preheader": "Your NUMU order is confirmed",
    },
}


def order_confirmed_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _CONFIRMED.get(language, _CONFIRMED["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )
    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>
        <hr class="divider">
        <p><strong>{c["what_next"]}</strong></p>
        <p>{c["what_next_body"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


# ─────────────────────────────────────────────────────────────────────────
# 5. Processing
# ─────────────────────────────────────────────────────────────────────────

_PROCESSING = {
    "ar": {
        "title": "طلبك بيتجهّز",
        "subtitle": "قرّب يتشحن",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> بيتجهّز ويتغلّف للشحن.",
        "note": "هنبعتلك رسالة تانية أول ما الطلب يتشحن.",
        "preheader": "طلبك بيتجهّز",
    },
    "en": {
        "title": "Your Order is Being Prepared",
        "subtitle": "Almost ready to ship",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> is now being prepared and packed for shipping.",
        "note": "We'll send you another email once your order has been shipped.",
        "preheader": "Your NUMU order is being prepared",
    },
}


def order_processing_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _PROCESSING.get(language, _PROCESSING["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )
    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>
        <hr class="divider">
        <p>{c["note"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


# ─────────────────────────────────────────────────────────────────────────
# 6. Cancelled
# ─────────────────────────────────────────────────────────────────────────

_CANCELLED = {
    "ar": {
        "title": "تم إلغاء طلبك",
        "subtitle": "نأسف لإلغاء الطلب",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong> تم إلغاؤه.",
        "reason_label": "السبب",
        "note": "لو ماطلبتش الإلغاء ده أو عندك أي استفسار، تواصل مع المتجر في أي وقت.",
        "refund_note": "لو الدفع اتعمل خلاص، هيتم استرداد المبلغ تلقائياً.",
        "preheader": "طلبك اتلغى",
    },
    "en": {
        "title": "Your Order Has Been Cancelled",
        "subtitle": "We're sorry to see this order go",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "Your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been cancelled.",
        "reason_label": "Reason",
        "note": "If you didn't request this cancellation or have questions, please contact the store.",
        "refund_note": "If payment was already processed, a refund will be issued automatically.",
        "preheader": "Your NUMU order was cancelled",
    },
}


def order_cancelled_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    reason: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _CANCELLED.get(language, _CANCELLED["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    reason_section = ""
    if reason:
        reason_section = f"""
        <div class="panel" style="border-{("right" if language == "ar" else "left")}-color:#C2362F;">
            <p class="label">{c["reason_label"]}</p>
            <p class="value" style="font-size:14px; color:#C2362F;">{reason}</p>
        </div>"""

    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>
        {reason_section}
        <hr class="divider">
        <p>{c["note"]}</p>
        <p class="muted">{c["refund_note"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


# ─────────────────────────────────────────────────────────────────────────
# 7. Refunded
# ─────────────────────────────────────────────────────────────────────────

_REFUNDED = {
    "ar": {
        "title": "تم استرداد المبلغ",
        "subtitle": "الاسترداد في الطريق",
        "greeting": "أهلاً {customer_name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": "تم استرداد مبلغ طلبك <strong>#{order_number}</strong> من <strong>{store_name}</strong>.",
        "reason_label": "السبب",
        "note": "المبلغ هيظهر في حسابك خلال ٥ إلى ١٠ أيام عمل حسب البنك بتاعك.",
        "preheader": "تم استرداد مبلغك",
    },
    "en": {
        "title": "Your Refund Has Been Processed",
        "subtitle": "Refund on its way",
        "greeting": "Hi {customer_name},",
        "greeting_default": "Hi there,",
        "intro": "A refund for your order <strong>#{order_number}</strong> from <strong>{store_name}</strong> has been processed.",
        "reason_label": "Reason",
        "note": "The refund should appear in your account within 5–10 business days depending on your bank.",
        "preheader": "Your NUMU refund was processed",
    },
}


def order_refunded_html(
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    reason: str | None = None,
    language: str = "ar",
    **_kwargs,
) -> str:
    c = _REFUNDED.get(language, _REFUNDED["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )

    reason_section = ""
    if reason:
        reason_section = f"""
        <div class="panel">
            <p class="label">{c["reason_label"]}</p>
            <p class="value" style="font-size:14px;">{reason}</p>
        </div>"""

    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"].format(order_number=order_number, store_name=store_name)}</p>
        {reason_section}
        <hr class="divider">
        <p>{c["note"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


# ─────────────────────────────────────────────────────────────────────────
# Unified status-based email factory
# ─────────────────────────────────────────────────────────────────────────

_STATUS_SUBJECTS = {
    "confirmed": {
        "ar": "تم تأكيد طلبك #{order_number} — {store_name}",
        "en": "Your Order #{order_number} is Confirmed — {store_name}",
    },
    "processing": {
        "ar": "طلبك #{order_number} بيتجهّز — {store_name}",
        "en": "Your Order #{order_number} is Being Prepared — {store_name}",
    },
    "shipped": {
        "ar": "طلبك #{order_number} اتشحن — {store_name}",
        "en": "Your Order #{order_number} Has Shipped — {store_name}",
    },
    "delivered": {
        "ar": "الطلب #{order_number} اتسلّم — {store_name}",
        "en": "Order #{order_number} Delivered — {store_name}",
    },
    "cancelled": {
        "ar": "تم إلغاء الطلب #{order_number} — {store_name}",
        "en": "Order #{order_number} Cancelled — {store_name}",
    },
    "refunded": {
        "ar": "تم استرداد مبلغ الطلب #{order_number} — {store_name}",
        "en": "Refund Processed for Order #{order_number} — {store_name}",
    },
}

_STATUS_HTML_FNS = {
    "confirmed": order_confirmed_html,
    "processing": order_processing_html,
    "shipped": shipping_notification_html,
    "delivered": delivery_confirmation_html,
    "cancelled": order_cancelled_html,
    "refunded": order_refunded_html,
}


def order_status_email(
    status: str,
    order_number: str,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    tracking_number: str | None = None,
    carrier: str | None = None,
    reason: str | None = None,
    language: str = "ar",
) -> dict | None:
    """Generate subject + HTML for any order status email.

    Returns {"subject": str, "html": str} or None if no template exists.
    """
    subjects = _STATUS_SUBJECTS.get(status)
    html_fn = _STATUS_HTML_FNS.get(status)

    if not subjects or not html_fn:
        return None

    lang = language if language in subjects else "ar"
    subject = subjects[lang].format(order_number=order_number, store_name=store_name)

    html = html_fn(
        order_number=order_number,
        store_name=store_name,
        customer_name=customer_name,
        tracking_number=tracking_number,
        carrier=carrier,
        reason=reason,
        language=lang,
    )

    return {"subject": subject, "html": html}


# Suppress unused-import warnings — these constants are re-exported for callers.
__all__ = [
    "ORDER_CONFIRMATION_TEMPLATE",
    "SHIPPING_NOTIFICATION_TEMPLATE",
    "DELIVERY_CONFIRMATION_TEMPLATE",
    "order_status_email",
    "order_confirmation_html",
    "shipping_notification_html",
    "delivery_confirmation_html",
    "order_confirmed_html",
    "order_processing_html",
    "order_cancelled_html",
    "order_refunded_html",
]
