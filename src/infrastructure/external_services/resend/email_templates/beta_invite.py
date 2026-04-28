"""Beta invite email template.

Sent to waitlist entries when an admin invites them to create a store.
Egyptian Arabic is the default language.
"""

from src.infrastructure.external_services.resend.email_templates._base import (
    NAVY,
    header,
    wrap,
)

_BETA_INVITE = {
    "ar": {
        "title": "مدعو تنضم لـ نُمو",
        "subtitle": "وصول مبكر لتجار البيتا",
        "greeting": "أهلاً {name}،",
        "greeting_default": "أهلاً بيك،",
        "intro": 'خبر حلو — اتختارت تنضم لـ <strong>برنامج البيتا الخاص بـ <span class="brand">نُمو</span></strong>. تقدر دلوقتي تعمل متجرك وتبدأ تبيع على أحدث منصة تجارة إلكترونية في مصر.',
        "code_label": "كود الدعوة بتاعك",
        "use_code": "استخدم الكود ده وانت بتعمل متجرك. الكود ده مرة واحدة بس ومربوط بحسابك.",
        "btn": "ابدأ متجرك دلوقتي",
        "perks_title": "كتاجر بيتا بتاخد:",
        "perks": [
            "وصول كامل للمنصة — متجر، دفع، شحن",
            "بوابات الدفع المصرية (باي موب، فوري، الدفع عند الاستلام)",
            "فاتورة إلكترونية متوافقة مع مصلحة الضرايب",
            "دعم فني بأولوية طول فترة البيتا",
            "أسعار التجار المؤسسين (مثبتة معاك للأبد)",
        ],
        "expiry": "الدعوة دي صلاحيتها ٧ أيام. لو عندك أي استفسار، رد على الإيميل ده وهنرد عليك خلال ٢٤ ساعة.",
        "preheader": "دعوتك لنُمو وصلت — وصول مبكر للتجار",
        "subject": "مدعو تنضم لـ نُمو بيتا",
    },
    "en": {
        "title": "You're Invited to NUMU",
        "subtitle": "Early Merchant Access — Beta Program",
        "greeting": "Hi {name},",
        "greeting_default": "Hi there,",
        "intro": "Great news — you've been selected for the <strong>NUMU beta program</strong>. You can now create your store and start selling with Egypt's next-generation e-commerce platform.",
        "code_label": "Your Beta Invite Code",
        "use_code": "Use this code when creating your store. It's single-use and tied to your account.",
        "btn": "Create Your Store",
        "perks_title": "What you get as a beta merchant:",
        "perks": [
            "Full platform access — storefront, payments, shipping",
            "Egyptian payment gateways (Paymob, Fawry, COD)",
            "ETA e-invoicing compliance built in",
            "Priority support during beta",
            "Founding merchant pricing (locked in forever)",
        ],
        "expiry": "This invite expires in 7 days. If you have questions, reply to this email and we'll get back to you within 24 hours.",
        "preheader": "Your NUMU beta invite has arrived",
        "subject": "You're invited to NUMU Beta",
    },
}


def beta_invite_html(
    name: str | None,
    invite_code: str,
    language: str = "ar",
) -> str:
    """Render the beta invite email."""
    c = _BETA_INVITE.get(language, _BETA_INVITE["ar"])
    greeting = c["greeting"].format(name=name) if name else c["greeting_default"]
    code_display = invite_code[:16]

    perks_html = "".join(f'<li style="margin-bottom:8px;">{p}</li>' for p in c["perks"])

    body = f"""
    {header(c["title"], c["subtitle"], language=language)}
    <div class="body">
        <p class="lead">{greeting}</p>
        <p>{c["intro"]}</p>

        <div class="code-box">
            <p style="margin:0 0 8px; font-size:11px; color:#6C757D; text-transform:uppercase; letter-spacing:1.2px;">
                {c["code_label"]}
            </p>
            <p class="digits" style="font-size:24px; letter-spacing:3px; color:{NAVY}; word-break:break-all;">{code_display}</p>
        </div>

        <p>{c["use_code"]}</p>

        <p class="center" style="margin:30px 0;">
            <a href="https://numueg.app/accept-invite?code={invite_code}" class="btn">{c["btn"]}</a>
        </p>

        <hr class="divider">

        <p style="font-weight:600; color:{NAVY};">{c["perks_title"]}</p>
        <ul style="margin:8px 0 16px; padding-{("right" if language == "ar" else "left")}:22px; font-size:14px; color:#495057;">
            {perks_html}
        </ul>

        <p class="muted" style="margin-top:24px;">{c["expiry"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


def beta_invite_subject(language: str = "ar") -> str:
    return _BETA_INVITE.get(language, _BETA_INVITE["ar"])["subject"]
