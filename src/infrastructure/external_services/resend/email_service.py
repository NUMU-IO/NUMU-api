"""Resend email service implementation."""

import resend

from src.config import settings
from src.config.logging_config import get_logger
from src.core.exceptions import ExternalServiceError
from src.core.interfaces.services.email_service import EmailMessage, IEmailService

logger = get_logger(__name__)

# Resend errors that indicate a permanent configuration problem (no point retrying).
_PERMANENT_ERROR_PHRASES = [
    "domain with your API key is not verified",
    "api key is invalid",
    "api_key is required",
    "missing api key",
]


class EmailConfigurationError(ExternalServiceError):
    """Raised for permanent email configuration problems (non-retryable)."""


class ResendEmailService(IEmailService):
    """Email service implementation using Resend."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.resend_api_key
        if self.api_key:
            resend.api_key = self.api_key
        self.from_email = settings.email_from_address
        self.from_name = settings.email_from_name

    async def send_email(self, message: EmailMessage) -> bool:
        """Send an email using Resend."""
        to_list = message.to if isinstance(message.to, list) else [message.to]

        # ── Dev / missing-key guard ─────────────────────────────────
        if not self.api_key:
            logger.warning(
                "email_skipped_no_api_key",
                to=to_list,
                subject=message.subject,
                hint="Set RESEND_API_KEY in .env to enable email sending.",
            )
            return True  # Don't block the caller in dev

        try:
            from_address = f"{message.from_name or self.from_name} <{message.from_email or self.from_email}>"

            params = {
                "from": from_address,
                "to": to_list,
                "subject": message.subject,
            }

            # Template-based email
            if message.template_id:
                params["template"] = {
                    "id": message.template_id,
                    "props": message.context or {},
                }
            else:
                params["html"] = message.html_content

            if message.text_content:
                params["text"] = message.text_content
            if message.reply_to:
                params["reply_to"] = message.reply_to
            if message.attachments:
                params["attachments"] = message.attachments

            resend.Emails.send(params)
        except Exception as e:
            error_msg = str(e).lower()
            if any(phrase in error_msg for phrase in _PERMANENT_ERROR_PHRASES):
                raise EmailConfigurationError(
                    "Resend",
                    f"{e}  -- This is a configuration issue; fix RESEND_API_KEY / "
                    f"EMAIL_FROM_ADDRESS or verify the domain in Resend dashboard.",
                )
            raise ExternalServiceError("Resend", str(e))

        # Log success OUTSIDE the try/except — structlog can crash on
        # non-ASCII subjects (Arabic) when the Windows console uses cp1252.
        # That encoding error must NOT be mis-reported as an email failure.
        try:
            logger.info("email_sent", to=to_list, subject=message.subject)
        except UnicodeEncodeError:
            logger.info("email_sent", to=to_list, subject="(non-ascii subject)")
        return True

    async def send_verification_email(
        self, email: str, token: str, code: str | None = None
    ) -> bool:
        """Send email verification email with a 6-digit code and a verification link.

        Egyptian Arabic by default — matches NUMU brand identity.
        """
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        verify_url = f"{settings.cors_origins[0]}/verify-email?token={token}"
        code_display = code or "------"

        body = f"""
        {header("تأكيد البريد الإلكتروني", "خطوة واحدة وخلصت", language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك في <span class="brand">نُمو</span>،</p>
            <p>دخّل الكود ده في لوحة التحكم عشان تأكّد إيميلك:</p>

            <div class="code-box">
                <p class="digits">{code_display}</p>
                <p class="hint">الكود ده صلاحيته ٢٤ ساعة</p>
            </div>

            <hr class="divider">

            <p>أو اضغط الزرار ده عشان تأكّد على طول:</p>
            <p class="center" style="margin:20px 0;">
                <a href="{verify_url}" class="btn">تأكيد الإيميل</a>
            </p>

            <p class="muted" style="margin-top:24px;">
                لو ماعملتش حساب على نُمو، تجاهل الإيميل ده ببساطة.
            </p>
        </div>"""

        html_content = wrap(body, language="ar", preheader="كود تأكيد إيميلك على نُمو")
        message = EmailMessage(
            to=email,
            subject="تأكيد إيميلك على نُمو",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_staff_invitation_email(
        self,
        email: str,
        invite_url: str,
        tenant_name: str,
        inviter_name: str | None = None,
        personal_message: str | None = None,
    ) -> bool:
        """Send a staff invitation email with acceptance link."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        inviter_line = (
            f"<strong>{inviter_name}</strong> دعاك" if inviter_name else "اتدعيت"
        )

        personal_html = ""
        if personal_message:
            personal_html = (
                '<div class="panel" style="margin:16px 0;">'
                f'<p style="margin:0;">{personal_message}</p>'
                "</div>"
            )

        body = f"""
        {header("دعوة لنضم للفريق", tenant_name, language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك،</p>
            <p>
                {inviter_line} تنضم لفريق <strong>{tenant_name}</strong>
                على <span class="brand">نُمو</span>.
            </p>
            {personal_html}
            <p>اضغط الزرار ده عشان تقبل الدعوة وتبدأ:</p>
            <p class="center" style="margin:28px 0;">
                <a href="{invite_url}" class="btn">قبول الدعوة</a>
            </p>

            <hr class="divider">

            <p class="muted">الدعوة دي صلاحيتها ٧ أيام.</p>
            <p class="muted">
                لو الزرار مش شغال، افتح اللينك ده:<br>
                <a href="{invite_url}">{invite_url}</a>
            </p>
        </div>"""

        html_content = wrap(
            body,
            language="ar",
            preheader=f"دعوة للانضمام لـ {tenant_name} على نُمو",
        )
        message = EmailMessage(
            to=email,
            subject=f"دعوة للانضمام لـ {tenant_name} — نُمو",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_password_reset_email(self, email: str, token: str) -> bool:
        """Send password reset email — Egyptian Arabic, NUMU brand."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        reset_url = f"{settings.cors_origins[0]}/reset-password?token={token}"

        body = f"""
        {header("إعادة تعيين كلمة المرور", "طلبنا تغيير الباسورد", language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك،</p>
            <p>وصلنا طلب لإعادة تعيين كلمة المرور بتاعتك على <span class="brand">نُمو</span>. اضغط على الزرار ده عشان تظبط باسورد جديد:</p>

            <p class="center" style="margin:28px 0;">
                <a href="{reset_url}" class="btn">إعادة تعيين كلمة المرور</a>
            </p>

            <hr class="divider">

            <p class="muted">اللينك ده صلاحيته ساعة واحدة بس.</p>
            <p class="muted">لو ماطلبتش إعادة تعيين كلمة المرور، تجاهل الإيميل ده وحسابك في أمان.</p>
        </div>"""

        html_content = wrap(
            body, language="ar", preheader="إعادة تعيين كلمة المرور بتاعتك على نُمو"
        )
        message = EmailMessage(
            to=email,
            subject="إعادة تعيين كلمة المرور — نُمو",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_order_confirmation(
        self,
        email: str,
        order_number: str,
        order_details: dict,
        language: str = "ar",
    ) -> bool:
        """Send order confirmation email."""
        from src.infrastructure.external_services.resend.email_templates.notifications import (
            ORDER_CONFIRMATION_TEMPLATE,
        )

        items = order_details.get("items", [])
        total = order_details.get("total", 0)
        currency = order_details.get("currency", "EGP")
        store_name = order_details.get("store_name", "NUMU")
        customer_name = order_details.get("customer_name")

        html_content = ORDER_CONFIRMATION_TEMPLATE["html_fn"](
            order_number=order_number,
            items=items,
            total=total,
            currency=currency,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
        )
        subject = ORDER_CONFIRMATION_TEMPLATE["subject_fn"](
            order_number, store_name, language
        )

        message = EmailMessage(
            to=email,
            subject=subject,
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_invoice_email(
        self,
        email: str,
        order_number: str,
        invoice_number: str,
        pdf_bytes: bytes,
        store_name: str = "NUMU",
        language: str = "ar",
    ) -> bool:
        """Send invoice email with PDF attachment to customer."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        is_ar = language == "ar"
        subject = (
            f"فاتورتك من {store_name} — طلب #{order_number}"
            if is_ar
            else f"Your Invoice from {store_name} — Order #{order_number}"
        )

        if is_ar:
            body = f"""
            {header("فاتورة ضريبية", store_name, badge=f"#{invoice_number}", language="ar")}
            <div class="body">
                <p class="lead">أهلاً بيك،</p>
                <p>
                  شكراً لطلبك رقم <strong>#{order_number}</strong>.
                  مرفق فاتورتك الضريبية رقم <strong>{invoice_number}</strong>.
                </p>

                <div class="panel">
                    <p style="margin:0; font-size:14px; color:#1A1A2E;">
                        📎 الفاتورة مرفقة كملف PDF. تقدر تحمّلها وتحتفظ بيها لسجلاتك.
                    </p>
                </div>

                <p class="muted" style="margin-top:24px;">
                    دي فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرايب المصرية.
                </p>
            </div>"""
        else:
            body = f"""
            {header("Tax Invoice", store_name, badge=f"#{invoice_number}", language="en")}
            <div class="body">
                <p class="lead">Hello,</p>
                <p>
                  Thank you for your order <strong>#{order_number}</strong>.
                  Please find attached your tax invoice <strong>{invoice_number}</strong>.
                </p>

                <div class="panel">
                    <p style="margin:0; font-size:14px; color:#1A1A2E;">
                        📎 Your invoice is attached as a PDF file. You may download it for your records.
                    </p>
                </div>

                <p class="muted" style="margin-top:24px;">
                    This is an electronic invoice issued per Egyptian Tax Authority requirements.
                </p>
            </div>"""

        html_content = wrap(
            body,
            language=language,
            preheader=(
                f"فاتورتك رقم {invoice_number}"
                if is_ar
                else f"Your invoice {invoice_number}"
            ),
        )

        import base64

        safe_filename = invoice_number.replace("/", "-")
        message = EmailMessage(
            to=email,
            subject=subject,
            html_content=html_content,
            attachments=[
                {
                    "filename": f"{safe_filename}.pdf",
                    "content": base64.b64encode(pdf_bytes).decode("utf-8"),
                    "content_type": "application/pdf",
                }
            ],
        )
        return await self.send_email(message)

    async def send_shipping_notification(
        self,
        email: str,
        order_number: str,
        tracking_number: str | None,
        carrier: str | None,
        language: str = "ar",
    ) -> bool:
        """Send shipping notification email."""
        from src.infrastructure.external_services.resend.email_templates.notifications import (
            SHIPPING_NOTIFICATION_TEMPLATE,
        )

        html_content = SHIPPING_NOTIFICATION_TEMPLATE["html_fn"](
            order_number=order_number,
            tracking_number=tracking_number,
            carrier=carrier,
            language=language,
        )
        subject = SHIPPING_NOTIFICATION_TEMPLATE["subject_fn"](
            order_number, language=language
        )

        message = EmailMessage(
            to=email,
            subject=subject,
            html_content=html_content,
        )
        return await self.send_email(message)
