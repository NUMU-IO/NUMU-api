"""Resend email service implementation."""

import resend

from src.config import settings
from src.core.exceptions import ExternalServiceError
from src.core.interfaces.services.email_service import EmailMessage, IEmailService


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
        try:
            to_list = message.to if isinstance(message.to, list) else [message.to]
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

            # Print email to terminal for development
            print("=" * 60)
            print(f"SENDING EMAIL TO: {to_list}")
            print(f"SUBJECT: {message.subject}")
            print(f"CONTENT:\n{message.html_content}")
            print("=" * 60)

            resend.Emails.send(params)
            return True
        except Exception as e:
            raise ExternalServiceError("Resend", str(e))

    async def send_verification_email(self, email: str, token: str) -> bool:
        """Send email verification email."""
        html_content = f"""
        <h1>Verify Your Email</h1>
        <p>Please click the link below to verify your email address:</p>
        <a href="{settings.cors_origins[0]}/verify-email?token={token}">Verify Email</a>
        <p>If you didn't create an account, please ignore this email.</p>
        """
        message = EmailMessage(
            to=email,
            subject="Verify Your Email - Octyrafiy",
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
