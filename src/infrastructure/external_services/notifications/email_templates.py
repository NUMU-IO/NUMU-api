"""Email templates for configuration notifications.

This module provides structured email templates for:
- Configuration request notifications (admin + merchant)
- Credential configuration notifications
- Status update notifications

Merchant-facing emails default to Egyptian Arabic ("ar"). The admin
template stays English (internal staff) but still uses the NUMU brand
chrome from `_base`.
"""

from dataclasses import dataclass

from src.infrastructure.external_services.resend.email_templates._base import (
    DANGER,
    NAVY,
    header,
    wrap,
)


@dataclass
class EmailTemplate:
    """Base email template."""

    subject: str
    html_body: str
    text_body: str


# ─────────────────────────────────────────────────────────────────────────
# Configuration request notifications
# ─────────────────────────────────────────────────────────────────────────


class ConfigurationRequestEmailTemplate:
    """Email templates for configuration request notifications."""

    @staticmethod
    def new_request_admin(
        merchant_name: str,
        service_name: str,
        service_type: str,
        priority: str,
        notes: str | None,
        action_url: str,
    ) -> EmailTemplate:
        """Internal admin notification — English, branded NUMU chrome."""
        subject = (
            f"[NUMU] New Configuration Request: {service_name} — {priority.upper()}"
        )

        priority_color = {
            "urgent": DANGER,
            "high": "#E07B16",
            "normal": "#1F8A4C",
        }.get(priority.lower(), NAVY)

        notes_section = (
            f'<div class="panel"><p class="label">Merchant Notes</p>'
            f'<p style="margin:4px 0 0; font-size:14px;">{notes}</p></div>'
            if notes
            else ""
        )

        body = f"""
        {header("New Configuration Request", "Admin action required", language="en")}
        <div class="body">
            <p class="lead">A merchant has requested service configuration:</p>

            <div class="panel">
                <p class="label">Merchant</p>
                <p style="margin:4px 0 14px; font-size:15px;">{merchant_name}</p>
                <p class="label">Service</p>
                <p style="margin:4px 0 14px; font-size:15px;">{service_name} • {service_type}</p>
                <p class="label">Priority</p>
                <p style="margin:4px 0 0; font-size:15px; color:{priority_color}; font-weight:700;">
                    {priority.upper()}
                </p>
            </div>

            {notes_section}

            <p>Please review and configure the credentials:</p>
            <p class="center" style="margin-top:24px;">
                <a href="{action_url}" class="btn">Configure Credentials</a>
            </p>
        </div>"""

        html_body = wrap(
            body,
            language="en",
            preheader=f"New {priority} request from {merchant_name}",
        )

        text_body = f"""
NUMU Admin — New Configuration Request

Merchant: {merchant_name}
Service: {service_name}
Type: {service_type}
Priority: {priority.upper()}
{f"Notes: {notes}" if notes else ""}

Configure credentials at:
{action_url}
        """.strip()

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)

    @staticmethod
    def request_received_merchant(
        merchant_name: str,
        service_name: str,
        request_id: str,
        language: str = "ar",
    ) -> EmailTemplate:
        """Merchant confirmation — Egyptian Arabic by default."""
        if language == "en":
            subject = f"[NUMU] Configuration Request Received: {service_name}"
            title = "Request Received"
            subtitle = "We're on it"
            greeting = f"Hi {merchant_name},"
            intro = (
                f"We've received your configuration request for "
                f"<strong>{service_name}</strong>."
            )
            body_text = (
                "Our team will review your request and configure the service for "
                "your store. This typically takes 1–2 business days."
            )
            ref_label = "Reference ID"
            outro = "You'll receive an email notification once the configuration is complete."
            thanks = "Thank you for choosing NUMU!"
            preheader = f"Your {service_name} request was received"
        else:
            subject = f"استلمنا طلبك لإعداد {service_name} — نُمو"
            title = "استلمنا طلبك"
            subtitle = "بنشتغل عليه"
            greeting = f"أهلاً {merchant_name}،"
            intro = f"استلمنا طلبك لإعداد <strong>{service_name}</strong>."
            body_text = "فريقنا هيراجع الطلب ويظبط الخدمة لمتجرك. ده عادةً بياخد من يوم لـ يومين عمل."
            ref_label = "رقم الطلب المرجعي"
            outro = "هتوصلك رسالة تانية أول ما الإعداد يخلص."
            thanks = 'شكراً إنك اخترت <span class="brand">نُمو</span>!'
            preheader = f"استلمنا طلبك لإعداد {service_name}"

        body = f"""
        {header(title, subtitle, language=language)}
        <div class="body">
            <p class="lead">{greeting}</p>
            <p>{intro}</p>
            <p>{body_text}</p>

            <div class="panel">
                <p class="label">{ref_label}</p>
                <p class="value" style="font-size:15px; font-family:monospace; word-break:break-all;">{request_id}</p>
            </div>

            <p>{outro}</p>
            <p>{thanks}</p>
        </div>"""

        html_body = wrap(body, language=language, preheader=preheader)

        text_body = f"""
{title}

{greeting}

{intro}

{body_text}

{ref_label}: {request_id}

{outro}
        """.strip()

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)


# ─────────────────────────────────────────────────────────────────────────
# Credentials configured notifications
# ─────────────────────────────────────────────────────────────────────────


class CredentialsConfiguredEmailTemplate:
    """Email templates for credential configuration notifications."""

    @staticmethod
    def credentials_ready(
        merchant_name: str,
        service_name: str,
        service_type: str,
        features: list[str],
        action_url: str,
        language: str = "ar",
    ) -> EmailTemplate:
        """Generate email when credentials are configured."""
        if language == "en":
            subject = f"[NUMU] {service_name} is Now Active 🎉"
            title = "Great News!"
            subtitle = f"{service_name} is ready"
            greeting = f"Hi {merchant_name},"
            intro = (
                f"Your <strong>{service_name}</strong> integration has been "
                f"configured and is ready to use."
            )
            features_label = "What's enabled"
            cta = "Go to Settings"
            help_text = "Need help? Our support team is here for you."
            preheader = f"{service_name} is now active on your store"
        else:
            subject = f"[نُمو] {service_name} اتفعّل دلوقتي 🎉"
            title = "خبر حلو"
            subtitle = f"{service_name} جاهز للاستخدام"
            greeting = f"أهلاً {merchant_name}،"
            intro = f"تكامل <strong>{service_name}</strong> اتظبط ودلوقتي جاهز تستخدمه."
            features_label = "اللي اتفعّل"
            cta = "روح للإعدادات"
            help_text = "محتاج مساعدة؟ فريق الدعم موجود عشانك."
            preheader = f"{service_name} اتفعّل في متجرك"

        features_html = "".join(
            f'<li style="margin-bottom:8px;">{f}</li>' for f in features
        )

        body = f"""
        {header(title, subtitle, language=language)}
        <div class="body">
            <p class="lead">{greeting}</p>
            <p>{intro}</p>

            <div class="panel">
                <p class="label">{features_label}</p>
                <ul style="margin:10px 0 0; padding-{("right" if language == "ar" else "left")}:22px; font-size:14px; color:#1A1A2E;">
                    {features_html}
                </ul>
            </div>

            <p class="center" style="margin-top:28px;">
                <a href="{action_url}" class="btn">{cta}</a>
            </p>

            <p class="muted" style="margin-top:24px;">{help_text}</p>
        </div>"""

        html_body = wrap(body, language=language, preheader=preheader)

        features_text = "\n".join(f"  - {f}" for f in features)
        text_body = f"""
{title} — {subtitle}

{greeting}

{intro}

{features_label}:
{features_text}

{cta}: {action_url}

{help_text}
        """.strip()

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)

    @staticmethod
    def credentials_revoked(
        merchant_name: str,
        service_name: str,
        reason: str,
        language: str = "ar",
    ) -> EmailTemplate:
        """Generate email when credentials are revoked."""
        if language == "en":
            subject = f"[NUMU] Important: {service_name} Configuration Update"
            title = "Configuration Update"
            subtitle = "Action may be required"
            greeting = f"Hi {merchant_name},"
            alert_intro = (
                f"<strong>Important:</strong> Your {service_name} configuration "
                f"has been deactivated."
            )
            reason_label = "Reason"
            note = (
                "If you need to re-enable this service, please submit a new "
                "configuration request through your dashboard."
            )
            contact = "If you have questions, please contact our support team."
            preheader = f"Your {service_name} configuration was deactivated"
        else:
            subject = f"[نُمو] مهم: تحديث على إعداد {service_name}"
            title = "تحديث على الإعدادات"
            subtitle = "ممكن يحتاج تدخل منك"
            greeting = f"أهلاً {merchant_name}،"
            alert_intro = f"<strong>مهم:</strong> إعداد {service_name} تم إيقافه."
            reason_label = "السبب"
            note = "لو محتاج تفعّل الخدمة دي تاني، ابعت طلب إعداد جديد من لوحة التحكم."
            contact = "لو عندك أي استفسار، تواصل مع فريق الدعم."
            preheader = f"إعداد {service_name} تم إيقافه"

        body = f"""
        {header(title, subtitle, language=language)}
        <div class="body">
            <p class="lead">{greeting}</p>

            <div class="panel" style="border-{("right" if language == "ar" else "left")}-color:{DANGER};">
                <p style="margin:0 0 10px; font-size:15px; color:#1A1A2E;">{alert_intro}</p>
                <p class="label" style="margin-top:12px;">{reason_label}</p>
                <p style="margin:4px 0 0; font-size:14px; color:{DANGER};">{reason}</p>
            </div>

            <p>{note}</p>
            <p class="muted">{contact}</p>
        </div>"""

        html_body = wrap(body, language=language, preheader=preheader)

        text_body = f"""
{title}

{greeting}

{alert_intro}

{reason_label}: {reason}

{note}

{contact}
        """.strip()

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)
