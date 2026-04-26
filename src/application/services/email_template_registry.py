"""Registry of customer-facing email events with default subjects + body
templates.

This module is the single source of truth for the merchant-customizable
email-template feature. For every supported event type it captures:

* a stable string identifier (mirrored as :class:`EmailEventType` enum value)
* the human-readable English/Arabic display labels used in the merchant UI
* the variables (with English developer-facing descriptions) that the body
  may reference
* sample data used for previews, send-test, and startup validation
* the default subject and body HTML in both ``ar`` and ``en``

The default body HTML is the *body only* — without the brand chrome
(``_header()`` / ``wrap()``) defined in
``src.infrastructure.external_services.resend.email_templates._base``. The
renderer wraps registry-default bodies with brand chrome at send time;
merchant-customized bodies render raw (no chrome).

Templates use **Jinja2** placeholders (``{{ var }}``). The Jinja2
:class:`~jinja2.sandbox.SandboxedEnvironment` is used both at preview /
send time and inside :func:`validate_registry` (called at app startup)
so a malformed default fails-fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

# ─────────────────────────────────────────────────────────────────────────
# Event type enum — stable string identifiers for every supported event.
# ─────────────────────────────────────────────────────────────────────────


class EmailEventType(StrEnum):
    """Stable identifiers for every customer-facing email event NUMU sends.

    Values are the same strings used as keys in
    :data:`EMAIL_EVENT_REGISTRY` and persisted on merchant-customized
    template rows.
    """

    ORDER_CONFIRMATION = "order_confirmation"
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_PROCESSING = "order_processing"
    SHIPPING_NOTIFICATION = "shipping_notification"
    DELIVERY_CONFIRMATION = "delivery_confirmation"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REFUNDED = "order_refunded"
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
    STAFF_INVITATION = "staff_invitation"
    INSTAPAY_PAYMENT_CONFIRMED = "instapay_payment_confirmed"
    INSTAPAY_PAYMENT_REJECTED = "instapay_payment_rejected"


# ─────────────────────────────────────────────────────────────────────────
# Spec dataclass
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventSpec:
    """Specification for a single email event.

    Attributes:
        event_type: Stable string identifier (matches an
            :class:`EmailEventType` value).
        label_en: Human-readable English label (merchant UI).
        label_ar: Human-readable Arabic label (merchant UI).
        variables: Map of variable name to English description (developer
            facing — not shown to customers).
        sample_data: Sample values for every variable in ``variables``.
            Used for previews, send-test, and startup validation. Every
            variable name MUST be present here.
        default_subject: Default subject keyed by language (``ar``/``en``).
            Jinja2 templates against ``sample_data``.
        default_html: Default body HTML keyed by language (``ar``/``en``).
            Jinja2 template; body-only (no ``<html>``/``<body>`` wrapper).
    """

    event_type: str
    label_en: str
    label_ar: str
    variables: dict[str, str]
    sample_data: dict[str, Any]
    default_subject: dict[str, str]
    default_html: dict[str, str]


# ─────────────────────────────────────────────────────────────────────────
# Common variable description bank (DRY — shared across order events)
# ─────────────────────────────────────────────────────────────────────────


_V_CUSTOMER_NAME = "Customer's display name (e.g. 'Ahmed Hassan')."
_V_ORDER_NUMBER = "Short, customer-visible order number (no '#' prefix)."
_V_STORE_NAME = "Merchant store name shown in the body and subject line."
_V_CURRENCY = "ISO currency code, e.g. 'EGP'."
_V_REASON = "Free-text reason supplied by the merchant."


# ─────────────────────────────────────────────────────────────────────────
# 1. order_confirmation
#    Snapshot of `order_confirmation_html` in
#    src/infrastructure/external_services/resend/email_templates/notifications.py.
#    Body-only — chrome is added by `_base.wrap()` at send time.
# ─────────────────────────────────────────────────────────────────────────


_ORDER_CONFIRMATION_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>استلمنا طلبك وبيتجهّز. دي تفاصيل اللي طلبته:</p>

    <table class="items">
        <thead>
            <tr>
                <th>المنتج</th>
                <th>الكمية</th>
                <th class="price">السعر</th>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td>{{ item.name }}</td>
                <td>{{ item.quantity }}</td>
                <td class="price">{{ currency }} {{ "{:,.2f}".format(item.price) }}</td>
            </tr>
            {% endfor %}
            <tr class="total">
                <td colspan="2">الإجمالي</td>
                <td class="price">{{ currency }} {{ "{:,.2f}".format(order_total) }}</td>
            </tr>
        </tbody>
    </table>

    <div class="panel">
        <p class="label">رقم الطلب</p>
        <p class="value">#{{ order_number }}</p>
    </div>

    <hr class="divider">

    <p><strong>إيه اللي هيحصل بعد كده؟</strong></p>
    <p>التاجر هيأكد طلبك في أقرب وقت. هتوصلك رسالة تانية أول ما الطلب يتشحن مع رقم التتبع.</p>

    <p class="center" style="margin-top:28px;">
        <a href="{{ track_url }}" class="btn-outline">متابعة حالة الطلب</a>
    </p>
</div>"""

_ORDER_CONFIRMATION_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>We've received your order and it's being prepared. Here's a summary:</p>

    <table class="items">
        <thead>
            <tr>
                <th>Item</th>
                <th>Qty</th>
                <th class="price">Price</th>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td>{{ item.name }}</td>
                <td>{{ item.quantity }}</td>
                <td class="price">{{ currency }} {{ "{:,.2f}".format(item.price) }}</td>
            </tr>
            {% endfor %}
            <tr class="total">
                <td colspan="2">Total</td>
                <td class="price">{{ currency }} {{ "{:,.2f}".format(order_total) }}</td>
            </tr>
        </tbody>
    </table>

    <div class="panel">
        <p class="label">Order Number</p>
        <p class="value">#{{ order_number }}</p>
    </div>

    <hr class="divider">

    <p><strong>What happens next?</strong></p>
    <p>The merchant will confirm your order shortly. You'll receive another email once your order ships with tracking details.</p>

    <p class="center" style="margin-top:28px;">
        <a href="{{ track_url }}" class="btn-outline">View Order Status</a>
    </p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 2. order_confirmed (merchant accepted pending order)
# ─────────────────────────────────────────────────────────────────────────


_ORDER_CONFIRMED_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>خبر حلو! طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong> اتأكّد وبيتجهّز دلوقتي.</p>
    <hr class="divider">
    <p><strong>إيه اللي هيحصل بعد كده؟</strong></p>
    <p>التاجر هيبدأ يجهّز طلبك. هتوصلك رسالة تانية أول ما الطلب يتشحن مع رقم التتبع.</p>
</div>"""

_ORDER_CONFIRMED_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>Great news! Your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> has been confirmed and is being prepared.</p>
    <hr class="divider">
    <p><strong>What happens next?</strong></p>
    <p>The merchant will begin processing your order. You'll receive another email once it ships with tracking details.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 3. order_processing
# ─────────────────────────────────────────────────────────────────────────


_ORDER_PROCESSING_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong> بيتجهّز ويتغلّف للشحن.</p>
    <hr class="divider">
    <p>هنبعتلك رسالة تانية أول ما الطلب يتشحن.</p>
</div>"""

_ORDER_PROCESSING_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>Your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> is now being prepared and packed for shipping.</p>
    <hr class="divider">
    <p>We'll send you another email once your order has been shipped.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 4. shipping_notification
# ─────────────────────────────────────────────────────────────────────────


_SHIPPING_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>خبر حلو! طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong> اتشحن.</p>

    <div class="panel" style="text-align:center;">
        <p class="label">{{ carrier }} • رقم التتبع</p>
        <p class="value" style="direction:ltr;letter-spacing:1px;">{{ tracking_number }}</p>
    </div>

    <div class="steps">
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">تم استلام الطلب</p>
                <p class="sub">وصلنا طلبك</p>
            </div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">تم التأكيد</p>
                <p class="sub">التاجر أكّد الطلب</p>
            </div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot active">&#10148;</div>
            <div class="step-text">
                <p class="title">تم الشحن</p>
                <p class="sub">الطرد بقى في السكة</p>
            </div>
        </div>
        <div class="step-line pending"></div>
        <div class="step">
            <div class="step-dot pending">4</div>
            <div class="step-text">
                <p class="title">تم التسليم</p>
                <p class="sub">هيوصلك قريب</p>
            </div>
        </div>
    </div>

    <hr class="divider">

    <p class="center" style="margin-top:18px;">
        <a href="#" class="btn">تتبع الطرد</a>
    </p>
</div>"""

_SHIPPING_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>Great news! Your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> has been shipped.</p>

    <div class="panel" style="text-align:center;">
        <p class="label">{{ carrier }} • Tracking Number</p>
        <p class="value" style="direction:ltr;letter-spacing:1px;">{{ tracking_number }}</p>
    </div>

    <div class="steps">
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">Order Placed</p>
                <p class="sub">We received your order</p>
            </div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">Order Confirmed</p>
                <p class="sub">Merchant confirmed your order</p>
            </div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot active">&#10148;</div>
            <div class="step-text">
                <p class="title">Shipped</p>
                <p class="sub">Your package is on its way</p>
            </div>
        </div>
        <div class="step-line pending"></div>
        <div class="step">
            <div class="step-dot pending">4</div>
            <div class="step-text">
                <p class="title">Delivered</p>
                <p class="sub">Arriving soon</p>
            </div>
        </div>
    </div>

    <hr class="divider">

    <p class="center" style="margin-top:18px;">
        <a href="#" class="btn">Track Your Package</a>
    </p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 5. delivery_confirmation
# ─────────────────────────────────────────────────────────────────────────


_DELIVERY_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong> اتسلّم بنجاح.</p>

    <div class="steps">
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">تم الطلب</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">تم التأكيد</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">تم الشحن</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">تم التسليم</p>
                <p class="sub">الطرد وصل بنجاح</p>
            </div>
        </div>
    </div>

    <div class="panel" style="text-align:center;">
        <p style="font-size:34px; margin:0;">&#127881;</p>
        <p style="font-size:16px; font-weight:700; color:#1F8A4C; margin:8px 0 0;">الطلب مكتمل</p>
    </div>

    <hr class="divider">

    <p>نتمنى يعجبك. لو في أي حاجة مش مظبوطة، تواصل معانا في أي وقت.</p>
    <p>شكراً إنك اشتريت من <strong>{{ store_name }}</strong>.</p>
</div>"""

_DELIVERY_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>Your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> has been delivered successfully.</p>

    <div class="steps">
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">Order Placed</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">Order Confirmed</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text"><p class="title">Shipped</p></div>
        </div>
        <div class="step-line done"></div>
        <div class="step">
            <div class="step-dot done">&#10003;</div>
            <div class="step-text">
                <p class="title">Delivered</p>
                <p class="sub">Package delivered successfully</p>
            </div>
        </div>
    </div>

    <div class="panel" style="text-align:center;">
        <p style="font-size:34px; margin:0;">&#127881;</p>
        <p style="font-size:16px; font-weight:700; color:#1F8A4C; margin:8px 0 0;">Order Complete</p>
    </div>

    <hr class="divider">

    <p>We hope you enjoy your purchase. If anything isn't right, please reach out.</p>
    <p>Thank you for shopping with <strong>{{ store_name }}</strong>.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 6. order_cancelled
# ─────────────────────────────────────────────────────────────────────────


_CANCELLED_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong> تم إلغاؤه.</p>

    <div class="panel" style="border-right-color:#C2362F;">
        <p class="label">السبب</p>
        <p class="value" style="font-size:14px; color:#C2362F;">{{ reason }}</p>
    </div>

    <hr class="divider">
    <p>لو ماطلبتش الإلغاء ده أو عندك أي استفسار، تواصل مع المتجر في أي وقت.</p>
    <p class="muted">لو الدفع اتعمل خلاص، هيتم استرداد المبلغ تلقائياً.</p>
</div>"""

_CANCELLED_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>Your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> has been cancelled.</p>

    <div class="panel" style="border-left-color:#C2362F;">
        <p class="label">Reason</p>
        <p class="value" style="font-size:14px; color:#C2362F;">{{ reason }}</p>
    </div>

    <hr class="divider">
    <p>If you didn't request this cancellation or have questions, please contact the store.</p>
    <p class="muted">If payment was already processed, a refund will be issued automatically.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 7. order_refunded
# ─────────────────────────────────────────────────────────────────────────


_REFUNDED_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>تم استرداد مبلغ طلبك <strong>#{{ order_number }}</strong> من <strong>{{ store_name }}</strong>.</p>

    <div class="panel">
        <p class="label">المبلغ</p>
        <p class="value">{{ currency }} {{ "{:,.2f}".format(refund_amount) }}</p>
    </div>

    <hr class="divider">
    <p>المبلغ هيظهر في حسابك خلال ٥ إلى ١٠ أيام عمل حسب البنك بتاعك.</p>
</div>"""

_REFUNDED_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>A refund for your order <strong>#{{ order_number }}</strong> from <strong>{{ store_name }}</strong> has been processed.</p>

    <div class="panel">
        <p class="label">Amount</p>
        <p class="value">{{ currency }} {{ "{:,.2f}".format(refund_amount) }}</p>
    </div>

    <hr class="divider">
    <p>The refund should appear in your account within 5–10 business days depending on your bank.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 8. email_verification — snapshot of `send_verification_email` in
#    src/infrastructure/external_services/resend/email_service.py
# ─────────────────────────────────────────────────────────────────────────


_EMAIL_VERIFICATION_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ user_name }}،</p>
    <p>دخّل الكود ده في لوحة التحكم عشان تأكّد إيميلك:</p>

    <div class="code-box">
        <p class="digits">{{ code }}</p>
        <p class="hint">الكود ده صلاحيته {{ expires_in_minutes }} دقيقة</p>
    </div>

    <hr class="divider">

    <p class="muted" style="margin-top:24px;">
        لو ماعملتش حساب على نُمو، تجاهل الإيميل ده ببساطة.
    </p>
</div>"""

_EMAIL_VERIFICATION_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ user_name }},</p>
    <p>Enter this code in the dashboard to verify your email:</p>

    <div class="code-box">
        <p class="digits">{{ code }}</p>
        <p class="hint">This code expires in {{ expires_in_minutes }} minutes</p>
    </div>

    <hr class="divider">

    <p class="muted" style="margin-top:24px;">
        If you didn't create an account on NUMU, you can safely ignore this email.
    </p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 9. password_reset
# ─────────────────────────────────────────────────────────────────────────


_PASSWORD_RESET_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ user_name }}،</p>
    <p>وصلنا طلب لإعادة تعيين كلمة المرور بتاعتك على <span class="brand">نُمو</span>. اضغط على الزرار ده عشان تظبط باسورد جديد:</p>

    <p class="center" style="margin:28px 0;">
        <a href="{{ reset_link }}" class="btn">إعادة تعيين كلمة المرور</a>
    </p>

    <hr class="divider">

    <p class="muted">اللينك ده صلاحيته {{ expires_in_minutes }} دقيقة بس.</p>
    <p class="muted">لو ماطلبتش إعادة تعيين كلمة المرور، تجاهل الإيميل ده وحسابك في أمان.</p>
</div>"""

_PASSWORD_RESET_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ user_name }},</p>
    <p>We received a request to reset your password on <span class="brand">NUMU</span>. Click the button below to set a new password:</p>

    <p class="center" style="margin:28px 0;">
        <a href="{{ reset_link }}" class="btn">Reset Password</a>
    </p>

    <hr class="divider">

    <p class="muted">This link expires in {{ expires_in_minutes }} minutes.</p>
    <p class="muted">If you didn't request a password reset, ignore this email — your account is safe.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 10. staff_invitation
# ─────────────────────────────────────────────────────────────────────────


_STAFF_INVITATION_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً بيك،</p>
    <p>
        <strong>{{ inviter_name }}</strong> دعاك تنضم لفريق <strong>{{ store_name }}</strong>
        على <span class="brand">نُمو</span> بصلاحيات <strong>{{ role }}</strong>.
    </p>
    <p>اضغط الزرار ده عشان تقبل الدعوة وتبدأ:</p>
    <p class="center" style="margin:28px 0;">
        <a href="{{ invite_link }}" class="btn">قبول الدعوة</a>
    </p>

    <hr class="divider">

    <p class="muted">الدعوة دي صلاحيتها ٧ أيام.</p>
    <p class="muted">
        لو الزرار مش شغال، افتح اللينك ده:<br>
        <a href="{{ invite_link }}">{{ invite_link }}</a>
    </p>
</div>"""

_STAFF_INVITATION_HTML_EN = """\
<div class="body">
    <p class="lead">Hi there,</p>
    <p>
        <strong>{{ inviter_name }}</strong> invited you to join the <strong>{{ store_name }}</strong>
        team on <span class="brand">NUMU</span> with the <strong>{{ role }}</strong> role.
    </p>
    <p>Click the button below to accept the invitation and get started:</p>
    <p class="center" style="margin:28px 0;">
        <a href="{{ invite_link }}" class="btn">Accept Invitation</a>
    </p>

    <hr class="divider">

    <p class="muted">This invitation is valid for 7 days.</p>
    <p class="muted">
        If the button doesn't work, open this link:<br>
        <a href="{{ invite_link }}">{{ invite_link }}</a>
    </p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 11. instapay_payment_confirmed
# ─────────────────────────────────────────────────────────────────────────


_INSTAPAY_CONFIRMED_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>
        أكدنا استلام دفعتك عبر انستاباي للطلب
        <strong>#{{ order_number }}</strong>
        بمبلغ <strong>{{ currency }} {{ "{:,.2f}".format(amount) }}</strong>.
        التاجر بدأ في تجهيز طلبك، وهتوصلك رسالة أخرى لما يتشحن.
    </p>

    <div class="panel">
        <p class="label">المتجر</p>
        <p class="value" style="font-size:16px;">{{ store_name }}</p>
    </div>
</div>"""

_INSTAPAY_CONFIRMED_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>
        We've confirmed your InstaPay payment for order
        <strong>#{{ order_number }}</strong> —
        <strong>{{ currency }} {{ "{:,.2f}".format(amount) }}</strong>.
        The merchant is preparing your order; you'll get another email when it ships.
    </p>

    <div class="panel">
        <p class="label">Store</p>
        <p class="value" style="font-size:16px;">{{ store_name }}</p>
    </div>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# 12. instapay_payment_rejected
# ─────────────────────────────────────────────────────────────────────────


_INSTAPAY_REJECTED_HTML_AR = """\
<div class="body">
    <p class="lead">أهلاً {{ customer_name }}،</p>
    <p>
        التاجر في <strong>{{ store_name }}</strong> راجع الإثبات اللي رفعته للطلب
        <strong>#{{ order_number }}</strong> ومقدرش يأكد الدفع
        بمبلغ <strong>{{ currency }} {{ "{:,.2f}".format(amount) }}</strong>.
    </p>

    <div class="panel">
        <p class="label">السبب</p>
        <p class="value">{{ reason }}</p>
    </div>

    <p>لو تعتقد إن في خطأ، تواصل مع التاجر مباشرة.</p>
</div>"""

_INSTAPAY_REJECTED_HTML_EN = """\
<div class="body">
    <p class="lead">Hi {{ customer_name }},</p>
    <p>
        The merchant at <strong>{{ store_name }}</strong> reviewed the proof you uploaded for order
        <strong>#{{ order_number }}</strong> and couldn't confirm the
        <strong>{{ currency }} {{ "{:,.2f}".format(amount) }}</strong> payment.
    </p>

    <div class="panel">
        <p class="label">Reason</p>
        <p class="value">{{ reason }}</p>
    </div>

    <p>If you believe this is a mistake, please contact the merchant directly.</p>
</div>"""


# ─────────────────────────────────────────────────────────────────────────
# Registry — single source of truth
# ─────────────────────────────────────────────────────────────────────────


EMAIL_EVENT_REGISTRY: dict[str, EventSpec] = {
    EmailEventType.ORDER_CONFIRMATION.value: EventSpec(
        event_type=EmailEventType.ORDER_CONFIRMATION.value,
        label_en="Order confirmation",
        label_ar="تأكيد الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "order_total": "Order grand total in major currency units (e.g. EGP, not piastres).",
            "currency": _V_CURRENCY,
            "store_name": _V_STORE_NAME,
            "items": "List of line items, each with `name` (str), `quantity` (int), and `price` (float in major units).",
            "track_url": "Absolute URL to the customer-facing order tracking page.",
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "order_total": 1250.00,
            "currency": "EGP",
            "store_name": "Cairo Threads",
            "items": [
                {"name": "Linen Shirt", "quantity": 1, "price": 850.00},
                {"name": "Cotton Cap", "quantity": 2, "price": 200.00},
            ],
            "track_url": "https://cairo-threads.numu.store/track/1042",
        },
        default_subject={
            "ar": "تم تأكيد طلبك #{{ order_number }} — {{ store_name }}",
            "en": "Order Confirmed #{{ order_number }} — {{ store_name }}",
        },
        default_html={
            "ar": _ORDER_CONFIRMATION_HTML_AR,
            "en": _ORDER_CONFIRMATION_HTML_EN,
        },
    ),
    EmailEventType.ORDER_CONFIRMED.value: EventSpec(
        event_type=EmailEventType.ORDER_CONFIRMED.value,
        label_en="Order accepted by merchant",
        label_ar="تأكيد المتجر للطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "تم تأكيد طلبك #{{ order_number }} — {{ store_name }}",
            "en": "Your Order #{{ order_number }} is Confirmed — {{ store_name }}",
        },
        default_html={
            "ar": _ORDER_CONFIRMED_HTML_AR,
            "en": _ORDER_CONFIRMED_HTML_EN,
        },
    ),
    EmailEventType.ORDER_PROCESSING.value: EventSpec(
        event_type=EmailEventType.ORDER_PROCESSING.value,
        label_en="Order processing",
        label_ar="جاري تجهيز الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "طلبك #{{ order_number }} بيتجهّز — {{ store_name }}",
            "en": "Your Order #{{ order_number }} is Being Prepared — {{ store_name }}",
        },
        default_html={
            "ar": _ORDER_PROCESSING_HTML_AR,
            "en": _ORDER_PROCESSING_HTML_EN,
        },
    ),
    EmailEventType.SHIPPING_NOTIFICATION.value: EventSpec(
        event_type=EmailEventType.SHIPPING_NOTIFICATION.value,
        label_en="Shipping notification",
        label_ar="إشعار شحن الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "tracking_number": "Carrier-issued tracking number for the shipment.",
            "carrier": "Carrier name (e.g. 'Bosta', 'Aramex').",
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "tracking_number": "BST-123456789",
            "carrier": "Bosta",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "طلبك #{{ order_number }} اتشحن — {{ store_name }}",
            "en": "Your Order #{{ order_number }} Has Shipped — {{ store_name }}",
        },
        default_html={
            "ar": _SHIPPING_HTML_AR,
            "en": _SHIPPING_HTML_EN,
        },
    ),
    EmailEventType.DELIVERY_CONFIRMATION.value: EventSpec(
        event_type=EmailEventType.DELIVERY_CONFIRMATION.value,
        label_en="Delivery confirmation",
        label_ar="تأكيد تسليم الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "الطلب #{{ order_number }} اتسلّم — {{ store_name }}",
            "en": "Order #{{ order_number }} Delivered — {{ store_name }}",
        },
        default_html={
            "ar": _DELIVERY_HTML_AR,
            "en": _DELIVERY_HTML_EN,
        },
    ),
    EmailEventType.ORDER_CANCELLED.value: EventSpec(
        event_type=EmailEventType.ORDER_CANCELLED.value,
        label_en="Order cancelled",
        label_ar="إلغاء الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "reason": _V_REASON,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "reason": "Out of stock",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "تم إلغاء الطلب #{{ order_number }} — {{ store_name }}",
            "en": "Order #{{ order_number }} Cancelled — {{ store_name }}",
        },
        default_html={
            "ar": _CANCELLED_HTML_AR,
            "en": _CANCELLED_HTML_EN,
        },
    ),
    EmailEventType.ORDER_REFUNDED.value: EventSpec(
        event_type=EmailEventType.ORDER_REFUNDED.value,
        label_en="Order refunded",
        label_ar="استرداد مبلغ الطلب",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "refund_amount": "Refund amount in major currency units (e.g. EGP, not piastres).",
            "currency": _V_CURRENCY,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "refund_amount": 1250.00,
            "currency": "EGP",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "تم استرداد مبلغ الطلب #{{ order_number }} — {{ store_name }}",
            "en": "Refund Processed for Order #{{ order_number }} — {{ store_name }}",
        },
        default_html={
            "ar": _REFUNDED_HTML_AR,
            "en": _REFUNDED_HTML_EN,
        },
    ),
    EmailEventType.EMAIL_VERIFICATION.value: EventSpec(
        event_type=EmailEventType.EMAIL_VERIFICATION.value,
        label_en="Email verification",
        label_ar="تأكيد البريد الإلكتروني",
        variables={
            "code": "6-digit numeric verification code (string).",
            "user_name": "Recipient display name.",
            "expires_in_minutes": "Number of minutes the code remains valid (int).",
        },
        sample_data={
            "code": "428913",
            "user_name": "Ahmed",
            "expires_in_minutes": 1440,
        },
        default_subject={
            "ar": "تأكيد إيميلك على نُمو",
            "en": "Verify your email — NUMU",
        },
        default_html={
            "ar": _EMAIL_VERIFICATION_HTML_AR,
            "en": _EMAIL_VERIFICATION_HTML_EN,
        },
    ),
    EmailEventType.PASSWORD_RESET.value: EventSpec(
        event_type=EmailEventType.PASSWORD_RESET.value,
        label_en="Password reset",
        label_ar="إعادة تعيين كلمة المرور",
        variables={
            "reset_link": "Absolute URL to the password-reset page (token already embedded).",
            "user_name": "Recipient display name.",
            "expires_in_minutes": "Number of minutes the link remains valid (int).",
        },
        sample_data={
            "reset_link": "https://numu.store/reset-password?token=abc123",
            "user_name": "Ahmed",
            "expires_in_minutes": 60,
        },
        default_subject={
            "ar": "إعادة تعيين كلمة المرور — نُمو",
            "en": "Reset your password — NUMU",
        },
        default_html={
            "ar": _PASSWORD_RESET_HTML_AR,
            "en": _PASSWORD_RESET_HTML_EN,
        },
    ),
    EmailEventType.STAFF_INVITATION.value: EventSpec(
        event_type=EmailEventType.STAFF_INVITATION.value,
        label_en="Staff invitation",
        label_ar="دعوة موظف",
        variables={
            "inviter_name": "Display name of the team member sending the invite.",
            "store_name": _V_STORE_NAME,
            "invite_link": "Absolute URL to the invite-acceptance page (token embedded).",
            "role": "Role being granted (e.g. 'admin', 'staff').",
        },
        sample_data={
            "inviter_name": "Mona Adel",
            "store_name": "Cairo Threads",
            "invite_link": "https://numu.store/staff/accept?token=xyz789",
            "role": "staff",
        },
        default_subject={
            "ar": "دعوة للانضمام لـ {{ store_name }} — نُمو",
            "en": "You're invited to join {{ store_name }} on NUMU",
        },
        default_html={
            "ar": _STAFF_INVITATION_HTML_AR,
            "en": _STAFF_INVITATION_HTML_EN,
        },
    ),
    EmailEventType.INSTAPAY_PAYMENT_CONFIRMED.value: EventSpec(
        event_type=EmailEventType.INSTAPAY_PAYMENT_CONFIRMED.value,
        label_en="InstaPay payment confirmed",
        label_ar="تأكيد دفع انستاباي",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "amount": "Payment amount in major currency units (e.g. EGP, not piastres).",
            "currency": _V_CURRENCY,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "amount": 1250.00,
            "currency": "EGP",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "تم استلام دفعتك — طلب #{{ order_number }} — {{ store_name }}",
            "en": "Payment received — Order #{{ order_number }} — {{ store_name }}",
        },
        default_html={
            "ar": _INSTAPAY_CONFIRMED_HTML_AR,
            "en": _INSTAPAY_CONFIRMED_HTML_EN,
        },
    ),
    EmailEventType.INSTAPAY_PAYMENT_REJECTED.value: EventSpec(
        event_type=EmailEventType.INSTAPAY_PAYMENT_REJECTED.value,
        label_en="InstaPay payment rejected",
        label_ar="رفض دفع انستاباي",
        variables={
            "customer_name": _V_CUSTOMER_NAME,
            "order_number": _V_ORDER_NUMBER,
            "amount": "Payment amount in major currency units (e.g. EGP, not piastres).",
            "currency": _V_CURRENCY,
            "reason": _V_REASON,
            "store_name": _V_STORE_NAME,
        },
        sample_data={
            "customer_name": "Ahmed Hassan",
            "order_number": "1042",
            "amount": 1250.00,
            "currency": "EGP",
            "reason": "Receipt is unreadable — please re-upload.",
            "store_name": "Cairo Threads",
        },
        default_subject={
            "ar": "تعذر تأكيد الدفع — طلب #{{ order_number }} — {{ store_name }}",
            "en": "Payment not confirmed — Order #{{ order_number }} — {{ store_name }}",
        },
        default_html={
            "ar": _INSTAPAY_REJECTED_HTML_AR,
            "en": _INSTAPAY_REJECTED_HTML_EN,
        },
    ),
}


# ─────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────


def get_event_spec(event_type: str) -> EventSpec:
    """Return the :class:`EventSpec` for ``event_type``.

    Raises:
        KeyError: when ``event_type`` is not registered.
    """
    return EMAIL_EVENT_REGISTRY[event_type]


def list_events() -> list[EventSpec]:
    """Return every registered event spec, sorted by ``event_type``."""
    return sorted(EMAIL_EVENT_REGISTRY.values(), key=lambda s: s.event_type)


def allowed_variables(event_type: str) -> set[str]:
    """Return the set of variable names allowed for ``event_type``."""
    return set(EMAIL_EVENT_REGISTRY[event_type].variables.keys())


# ─────────────────────────────────────────────────────────────────────────
# Startup-time validation — runs from main.py's lifespan hook.
# ─────────────────────────────────────────────────────────────────────────


_REQUIRED_LANGS = ("ar", "en")


def _build_env() -> SandboxedEnvironment:
    """Build the Jinja2 sandbox the renderer uses.

    StrictUndefined surfaces missing variables as TemplateError instead
    of silently rendering an empty string — exactly what we want at
    validation time, and what the runtime renderer should also use to
    catch typos in merchant-customized templates.
    """
    return SandboxedEnvironment(
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def validate_registry() -> None:
    """Validate every spec in :data:`EMAIL_EVENT_REGISTRY`.

    For each spec asserts:

    * every required language (``ar``, ``en``) has a subject and HTML body
    * every key in ``variables`` exists in ``sample_data``
    * the default subject and HTML render against ``sample_data`` via a
      :class:`~jinja2.sandbox.SandboxedEnvironment` without errors

    Raises:
        AssertionError: When the registry is malformed (missing language,
            missing sample value, etc).
        jinja2.TemplateError: When a default template fails to render.
    """
    env = _build_env()

    for event_type, spec in EMAIL_EVENT_REGISTRY.items():
        assert spec.event_type == event_type, (
            f"event_type mismatch: spec.event_type={spec.event_type!r} key={event_type!r}"
        )

        # Language coverage on subject + HTML.
        for lang in _REQUIRED_LANGS:
            assert lang in spec.default_subject, (
                f"{event_type}: default_subject missing language {lang!r}"
            )
            assert lang in spec.default_html, (
                f"{event_type}: default_html missing language {lang!r}"
            )

        # Variable / sample-data coverage.
        for var_name in spec.variables:
            assert var_name in spec.sample_data, (
                f"{event_type}: variable {var_name!r} declared in `variables` "
                f"but not present in `sample_data`"
            )

        # Render defaults against sample_data — fails fast if a template
        # references an unknown variable or has bad Jinja2 syntax.
        for lang in _REQUIRED_LANGS:
            try:
                env.from_string(spec.default_subject[lang]).render(spec.sample_data)
            except Exception as exc:
                raise AssertionError(
                    f"{event_type}.default_subject[{lang!r}] failed to render: {exc}"
                ) from exc
            try:
                env.from_string(spec.default_html[lang]).render(spec.sample_data)
            except Exception as exc:
                raise AssertionError(
                    f"{event_type}.default_html[{lang!r}] failed to render: {exc}"
                ) from exc


__all__ = [
    "EmailEventType",
    "EventSpec",
    "EMAIL_EVENT_REGISTRY",
    "get_event_spec",
    "list_events",
    "allowed_variables",
    "validate_registry",
]
