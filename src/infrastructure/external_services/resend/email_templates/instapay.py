"""Email templates for the InstaPay proof-verification flow.

Three customer-facing emails:

1. ``instapay_instructions_html`` — appended to the order-confirmation
   email (not stand-alone). Renders the IPA / QR / reference / expiry
   block so a customer who closed the tab still has what they need to
   pay.
2. ``payment_confirmed_html`` — fires when a proof is approved
   (auto or manual). Short; confirms the money arrived.
3. ``payment_rejected_html`` — fires when a merchant rejects a proof.
   Shows the reason and (when can_retry) a CTA back to the InstaPay
   page for re-upload.
"""

from __future__ import annotations

from datetime import datetime

from src.infrastructure.external_services.resend.email_templates._base import (
    header,
    wrap,
)

# ─────────────────────────────────────────────────────────────────────────
# 1. InstaPay instructions block (embedded in order confirmation)
# ─────────────────────────────────────────────────────────────────────────

_INSTA_INSTRUCTIONS = {
    "ar": {
        "title": "ادفع عبر انستاباي",
        "intro": "ارسل المبلغ إلى العنوان التالي من تطبيق البنك الخاص بك:",
        "ipa_label": "عنوان الدفع (IPA)",
        "ref_label": "الكود المرجعي (ضعه في الملاحظات)",
        "amount_label": "المبلغ",
        "expires_label": "ادفع قبل",
        "resume_btn": "رفع إثبات الدفع",
        "fallback_phone_label": "رقم احتياطي",
    },
    "en": {
        "title": "Pay with InstaPay",
        "intro": "Send the amount to this address from your bank app:",
        "ipa_label": "InstaPay address (IPA)",
        "ref_label": "Reference (put this in the notes)",
        "amount_label": "Amount",
        "expires_label": "Pay before",
        "resume_btn": "Upload payment proof",
        "fallback_phone_label": "Fallback phone",
    },
}


def instapay_instructions_html(
    *,
    ipa: str,
    reference_code: str,
    amount_cents: int,
    currency: str,
    expires_at: datetime | str | None,
    resume_url: str | None = None,
    fallback_phone: str | None = None,
    language: str = "ar",
) -> str:
    """Render the IPA / QR / reference block as HTML.

    Designed to slot into the main order-confirmation email body as a
    self-contained <div>. No <html>/<body> wrapper — the caller's
    template provides those.
    """
    c = _INSTA_INSTRUCTIONS.get(language, _INSTA_INSTRUCTIONS["ar"])
    amount_display = f"{currency} {amount_cents / 100:,.2f}"
    expires_display = ""
    if isinstance(expires_at, datetime):
        expires_display = expires_at.strftime("%Y-%m-%d %H:%M UTC")
    elif isinstance(expires_at, str):
        expires_display = expires_at

    fallback_row = ""
    if fallback_phone:
        fallback_row = (
            f"<p><strong>{c['fallback_phone_label']}:</strong> "
            f'<span style="font-family:monospace">{fallback_phone}</span></p>'
        )

    btn = ""
    if resume_url:
        btn = (
            f'<p class="center" style="margin-top:20px;">'
            f'<a href="{resume_url}" class="btn-outline">{c["resume_btn"]}</a>'
            f"</p>"
        )

    return f"""
    <div class="panel" style="margin-top:24px;">
        <p><strong>{c["title"]}</strong></p>
        <p>{c["intro"]}</p>
        <p><strong>{c["ipa_label"]}:</strong>
           <span style="font-family:monospace">{ipa}</span></p>
        <p><strong>{c["ref_label"]}:</strong>
           <span style="font-family:monospace">{reference_code}</span></p>
        <p><strong>{c["amount_label"]}:</strong> {amount_display}</p>
        {"<p><strong>" + c["expires_label"] + ":</strong> " + expires_display + "</p>" if expires_display else ""}
        {fallback_row}
        {btn}
    </div>"""


# ─────────────────────────────────────────────────────────────────────────
# 2. Payment confirmed
# ─────────────────────────────────────────────────────────────────────────

_PAYMENT_CONFIRMED = {
    "ar": {
        "title": "تم استلام دفعتك",
        "subtitle": "الدفع تمام، الطلب في الطريق",
        "greeting_default": "أهلاً بيك،",
        "greeting": "أهلاً {customer_name}،",
        "body": (
            "أكدنا استلام دفعتك عبر انستاباي للطلب "
            "<strong>#{order_number}</strong> "
            "بمبلغ <strong>{amount}</strong>. "
            "التاجر بدأ في تجهيز طلبك، وهتوصلك رسالة أخرى لما يتشحن."
        ),
        "ref_label": "الكود المرجعي",
        "preheader": "تم تأكيد دفعتك عبر انستاباي",
    },
    "en": {
        "title": "Payment Received",
        "subtitle": "Your order is being prepared",
        "greeting_default": "Hi there,",
        "greeting": "Hi {customer_name},",
        "body": (
            "We've confirmed your InstaPay payment for order "
            "<strong>#{order_number}</strong> — "
            "<strong>{amount}</strong>. "
            "The merchant is preparing your order; you'll get another "
            "email when it ships."
        ),
        "ref_label": "Reference",
        "preheader": "Your InstaPay payment is confirmed",
    },
}


def payment_confirmed_html(
    *,
    order_number: str,
    reference_code: str,
    amount_cents: int,
    currency: str = "EGP",
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
) -> str:
    c = _PAYMENT_CONFIRMED.get(language, _PAYMENT_CONFIRMED["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )
    amount = f"{currency} {amount_cents / 100:,.2f}"
    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["body"].format(order_number=order_number, amount=amount)}</p>

        <div class="panel">
            <p class="label">{c["ref_label"]}</p>
            <p class="value" style="font-family:monospace">{reference_code}</p>
        </div>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def payment_confirmed_subject(
    order_number: str, store_name: str = "NUMU", language: str = "ar"
) -> str:
    if language == "en":
        return f"Payment received — Order #{order_number} — {store_name}"
    return f"تم استلام دفعتك — طلب #{order_number} — {store_name}"


# ─────────────────────────────────────────────────────────────────────────
# 3. Payment rejected
# ─────────────────────────────────────────────────────────────────────────

_PAYMENT_REJECTED = {
    "ar": {
        "title": "تعذر تأكيد دفعتك",
        "subtitle": "نحتاج إثبات جديد",
        "greeting_default": "أهلاً بيك،",
        "greeting": "أهلاً {customer_name}،",
        "body": (
            "التاجر راجع الإثبات اللي رفعته للطلب "
            "<strong>#{order_number}</strong> ومقدرش يأكد الدفع."
        ),
        "reason_label": "السبب",
        "retry_body": (
            "ممكن ترفع إثبات جديد خلال المدة المتبقية للدفع من الرابط "
            "ده. لو في أي استفسار، تواصل مع التاجر مباشرة."
        ),
        "no_retry_body": ("لو تعتقد إن في خطأ، تواصل مع التاجر مباشرة."),
        "btn": "رفع إثبات جديد",
        "preheader": "تعذر تأكيد دفعتك عبر انستاباي",
    },
    "en": {
        "title": "We couldn't confirm your payment",
        "subtitle": "A new proof is needed",
        "greeting_default": "Hi there,",
        "greeting": "Hi {customer_name},",
        "body": (
            "The merchant reviewed the proof you uploaded for order "
            "<strong>#{order_number}</strong> and couldn't confirm the "
            "payment."
        ),
        "reason_label": "Reason",
        "retry_body": (
            "You can upload a new proof using the link below. If you "
            "have any questions, please contact the merchant directly."
        ),
        "no_retry_body": (
            "If you believe this is a mistake, please contact the merchant directly."
        ),
        "btn": "Upload new proof",
        "preheader": "Your InstaPay proof was rejected",
    },
}


def payment_rejected_html(
    *,
    order_number: str,
    reason: str,
    can_retry: bool = True,
    retry_url: str | None = None,
    store_name: str = "NUMU",
    customer_name: str | None = None,
    language: str = "ar",
) -> str:
    c = _PAYMENT_REJECTED.get(language, _PAYMENT_REJECTED["ar"])
    greeting = (
        c["greeting"].format(customer_name=customer_name)
        if customer_name
        else c["greeting_default"]
    )
    body_copy = c["retry_body"] if can_retry and retry_url else c["no_retry_body"]
    cta = (
        f'<p class="center" style="margin-top:28px;">'
        f'<a href="{retry_url}" class="btn-outline">{c["btn"]}</a></p>'
        if can_retry and retry_url
        else ""
    )
    body = f"""
    {header(c["title"], c["subtitle"], badge=f"#{order_number}", language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["body"].format(order_number=order_number)}</p>

        <div class="panel">
            <p class="label">{c["reason_label"]}</p>
            <p class="value">{reason}</p>
        </div>

        <p>{body_copy}</p>
        {cta}
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def payment_rejected_subject(
    order_number: str, store_name: str = "NUMU", language: str = "ar"
) -> str:
    if language == "en":
        return f"Payment not confirmed — Order #{order_number} — {store_name}"
    return f"تعذر تأكيد الدفع — طلب #{order_number} — {store_name}"
