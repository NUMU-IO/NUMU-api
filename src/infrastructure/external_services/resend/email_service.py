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
                "html": message.html_content,
            }

            if message.text_content:
                params["text"] = message.text_content
            if message.reply_to:
                params["reply_to"] = message.reply_to
            if message.attachments:
                params["attachments"] = message.attachments

            resend.Emails.send(params)
            logger.info("email_sent", to=to_list, subject=message.subject)
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if any(phrase in error_msg for phrase in _PERMANENT_ERROR_PHRASES):
                raise EmailConfigurationError(
                    "Resend",
                    f"{e}  -- This is a configuration issue; fix RESEND_API_KEY / "
                    f"EMAIL_FROM_ADDRESS or verify the domain in Resend dashboard.",
                )
            raise ExternalServiceError("Resend", str(e))

    async def send_verification_email(
        self, email: str, token: str, code: str | None = None
    ) -> bool:
        """Send email verification email with a 6-digit code and a verification link."""
        verify_url = f"{settings.cors_origins[0]}/verify-email?token={token}"
        code_display = code or "------"

        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:40px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#D4AF37,#1034A6);padding:32px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:28px;letter-spacing:1px;">NUMU</h1>
    <p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">Verify Your Email</p>
  </td></tr>
  <!-- Body -->
  <tr><td style="padding:36px 32px;">
    <p style="font-size:16px;color:#333;margin:0 0 20px;">Enter this verification code in your dashboard:</p>
    <!-- Code box -->
    <div style="text-align:center;margin:24px 0;">
      <div style="display:inline-block;background:#f8f9fa;border:2px dashed #D4AF37;border-radius:8px;padding:16px 32px;">
        <span style="font-size:36px;font-weight:bold;letter-spacing:12px;color:#1034A6;font-family:monospace;">{code_display}</span>
      </div>
      <p style="color:#999;font-size:13px;margin:12px 0 0;">This code expires in 24 hours</p>
    </div>
    <!-- Divider -->
    <div style="border-top:1px solid #eee;margin:28px 0;"></div>
    <p style="font-size:14px;color:#666;margin:0 0 16px;">Or click the button below to verify instantly:</p>
    <div style="text-align:center;margin:20px 0;">
      <a href="{verify_url}" style="display:inline-block;padding:14px 36px;background:#D4AF37;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px;">Verify My Email</a>
    </div>
    <p style="font-size:12px;color:#999;margin:24px 0 0;">If you didn't create a NUMU account, you can safely ignore this email.</p>
  </td></tr>
  <!-- Footer -->
  <tr><td style="padding:20px 32px;text-align:center;background:#f8f9fa;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;margin:0;">&copy; 2026 NUMU. All rights reserved.</p>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>
        """
        message = EmailMessage(
            to=email,
            subject="Verify Your Email - NUMU",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_password_reset_email(self, email: str, token: str) -> bool:
        """Send password reset email."""
        html_content = f"""
        <h1>Reset Your Password</h1>
        <p>Click the link below to reset your password:</p>
        <a href="{settings.cors_origins[0]}/reset-password?token={token}">Reset Password</a>
        <p>This link will expire in 1 hour.</p>
        <p>If you didn't request a password reset, please ignore this email.</p>
        """
        message = EmailMessage(
            to=email,
            subject="Reset Your Password - Octyrafiy",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_order_confirmation(
        self,
        email: str,
        order_number: str,
        order_details: dict,
        language: str = "en",
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
        is_ar = language == "ar"
        subject = (
            f"فاتورتك من {store_name} - طلب #{order_number}"
            if is_ar
            else f"Your Invoice from {store_name} - Order #{order_number}"
        )

        if is_ar:
            html_content = f"""
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:40px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <tr><td style="background:linear-gradient(135deg,#D4AF37,#1034A6);padding:32px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:28px;letter-spacing:1px;">{store_name}</h1>
    <p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">فاتورة ضريبية</p>
  </td></tr>
  <tr><td style="padding:36px 32px;text-align:right;">
    <p style="font-size:16px;color:#333;margin:0 0 20px;">مرحباً،</p>
    <p style="font-size:15px;color:#555;margin:0 0 16px;">
      شكراً لطلبك رقم <strong style="color:#1034A6;">#{order_number}</strong>.
      مرفق فاتورتك الضريبية رقم <strong>{invoice_number}</strong>.
    </p>
    <div style="background:#f8f9fa;border-radius:8px;padding:16px;margin:24px 0;border-right:4px solid #D4AF37;">
      <p style="margin:0;font-size:14px;color:#666;">
        📎 الفاتورة مرفقة كملف PDF. يمكنك تحميلها والاحتفاظ بها لسجلاتك.
      </p>
    </div>
    <p style="font-size:12px;color:#999;margin:24px 0 0;">
      هذه فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرائب المصرية.
    </p>
  </td></tr>
  <tr><td style="padding:20px 32px;text-align:center;background:#f8f9fa;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;margin:0;">&copy; 2026 {store_name}. جميع الحقوق محفوظة.</p>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
        else:
            html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:40px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <tr><td style="background:linear-gradient(135deg,#D4AF37,#1034A6);padding:32px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:28px;letter-spacing:1px;">{store_name}</h1>
    <p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">Tax Invoice</p>
  </td></tr>
  <tr><td style="padding:36px 32px;">
    <p style="font-size:16px;color:#333;margin:0 0 20px;">Hello,</p>
    <p style="font-size:15px;color:#555;margin:0 0 16px;">
      Thank you for your order <strong style="color:#1034A6;">#{order_number}</strong>.
      Please find attached your tax invoice <strong>{invoice_number}</strong>.
    </p>
    <div style="background:#f8f9fa;border-radius:8px;padding:16px;margin:24px 0;border-left:4px solid #D4AF37;">
      <p style="margin:0;font-size:14px;color:#666;">
        📎 Your invoice is attached as a PDF file. You may download it for your records.
      </p>
    </div>
    <p style="font-size:12px;color:#999;margin:24px 0 0;">
      This is an electronic invoice issued per Egyptian Tax Authority requirements.
    </p>
  </td></tr>
  <tr><td style="padding:20px 32px;text-align:center;background:#f8f9fa;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;margin:0;">&copy; 2026 {store_name}. All rights reserved.</p>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

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
        language: str = "en",
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
