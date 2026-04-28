"""Generic transactional email templates that previously had ad-hoc inline HTML.

Covers:
1. OTP code (storefront customer order confirmation, password reset code)
2. Password reset code (customer-facing)
3. Waitlist signup welcome (public waitlist endpoint)

All templates use the shared brand chrome from `_base` and default to
Egyptian Arabic.
"""

from src.infrastructure.external_services.resend.email_templates._base import (
    NAVY,
    header,
    wrap,
)

# ─────────────────────────────────────────────────────────────────────────
# 1. OTP Code (generic — used for order confirmation, password reset, etc.)
# ─────────────────────────────────────────────────────────────────────────


def otp_code_email(
    code: str,
    purpose: str = "order",  # "order" | "password_reset" | "phone" | "login"
    expires_minutes: int = 5,
) -> dict:
    """Render an OTP code email.

    Returns {"subject": str, "html": str}
    """
    titles = {
        "order": ("كود تأكيد الطلب", "أكّد طلبك في ثواني"),
        "password_reset": ("كود استعادة كلمة المرور", "خطوة واحدة وترجع لحسابك"),
        "phone": ("كود تأكيد رقم الموبايل", "أكّد رقمك في ثواني"),
        "login": ("كود الدخول", "كود الدخول لحسابك على نُمو"),
    }
    intros = {
        "order": "استخدم الكود ده عشان تأكّد طلبك:",
        "password_reset": "استخدم الكود ده عشان تظبط باسورد جديد:",
        "phone": "استخدم الكود ده عشان تأكّد رقم موبايلك:",
        "login": "استخدم الكود ده عشان تدخل على حسابك:",
    }
    subject_map = {
        "order": "كود تأكيد طلبك — نُمو",
        "password_reset": "كود استعادة كلمة المرور — نُمو",
        "phone": "كود تأكيد رقم الموبايل — نُمو",
        "login": "كود الدخول — نُمو",
    }

    title, subtitle = titles.get(purpose, titles["order"])
    intro = intros.get(purpose, intros["order"])
    subject = subject_map.get(purpose, subject_map["order"])

    body = f"""
    {header(title, subtitle, language="ar")}
    <div class="body">
        <p class="lead">أهلاً بيك،</p>
        <p>{intro}</p>

        <div class="code-box">
            <p class="digits">{code}</p>
            <p class="hint">الكود ده صلاحيته {expires_minutes} دقايق</p>
        </div>

        <hr class="divider">

        <p class="muted">لو ماطلبتش الكود ده، تجاهل الإيميل وحسابك في أمان.</p>
        <p class="muted">لا تشارك الكود ده مع أي حد — حتى لو ادعى إنه من فريق نُمو.</p>
    </div>"""
    return {
        "subject": subject,
        "html": wrap(body, language="ar", preheader=f"كود التحقق: {code}"),
    }


# ─────────────────────────────────────────────────────────────────────────
# 2. Waitlist signup welcome
# ─────────────────────────────────────────────────────────────────────────


def waitlist_welcome_email(name: str | None, referral_code: str) -> dict:
    """Render the public waitlist signup welcome email."""
    greeting = f"أهلاً {name}،" if name else "أهلاً بيك،"

    body = f"""
    {header("انت دلوقتي في قائمة الانتظار", "هنبعتلك دعوتك أول ما الدور يجي عليك", language="ar")}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>
            أهلاً بيك في قائمة انتظار <span class="brand">نُمو</span> — منصة التجارة
            الإلكترونية اللي بنبنيها للتجار المصريين، وانت من أوائل اللي هيجربوها.
        </p>

        <p>
            <strong>إيه اللي بعد كده؟</strong> فريقنا هيراجع طلبك وهيبعتلك إيميل دعوة منفصل
            بكود تفعيل خاص بيك تقدر تستخدمه عشان تعمل متجرك. الإيميل ده
            بس تأكيد إنك دخلت قائمة الانتظار.
        </p>

        <hr class="divider">

        <p style="font-weight:700; color:{NAVY};">قدّم في الترتيب — ادعُ تجار تانيين</p>
        <p style="font-size:13px; color:#495057;">
            كود الإحالة بتاعك (للمشاركة فقط — مش بيفعّل حساب):
            <span style="font-family:monospace; background:#F1F3F5; padding:2px 8px; border-radius:4px; color:{NAVY}; letter-spacing:1px;">{referral_code}</span>
        </p>
        <p style="font-size:13px; color:#495057;">
            كل ما تاجر يسجل في قائمة الانتظار وهو ذاكر الكود ده، بتتقدم في الترتيب وبتاخد الدعوة بدري.
        </p>

        <hr class="divider">

        <p style="font-weight:700; color:{NAVY};">إيه اللي هتاخده كتاجر مؤسس؟</p>
        <ul style="margin:8px 0 16px; padding-right:22px; font-size:14px; color:#495057; line-height:1.9;">
            <li>وصول مبكر للمنصة قبل الإطلاق العام</li>
            <li>أسعار التجار المؤسسين مثبتة معاك للأبد</li>
            <li>دعم فني بأولوية طول فترة البيتا</li>
            <li>صوتك مسموع — اقتراحاتك بتأثر على المنتج</li>
        </ul>

        <p class="muted" style="margin-top:24px;">
            هنبعتلك إيميل الدعوة بكود التفعيل أول ما دورك يجي. لو عندك أي استفسار، رد على الإيميل ده.
        </p>
    </div>"""

    return {
        "subject": "انت في قائمة انتظار نُمو",
        "html": wrap(body, language="ar", preheader="انت دلوقتي في قائمة انتظار نُمو"),
    }
