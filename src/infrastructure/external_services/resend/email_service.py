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
    ) -> bool:
        """Send order confirmation email."""
        items_html = ""
        for item in order_details.get("items", []):
            items_html += f"""
            <tr>
                <td>{item['name']}</td>
                <td>{item['quantity']}</td>
                <td>${item['price']:.2f}</td>
            </tr>
            """
        
        html_content = f"""
        <h1>Order Confirmation</h1>
        <p>Thank you for your order!</p>
        <p><strong>Order Number:</strong> {order_number}</p>
        
        <h2>Order Details</h2>
        <table>
            <thead>
                <tr>
                    <th>Item</th>
                    <th>Quantity</th>
                    <th>Price</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>
        
        <p><strong>Total:</strong> ${order_details.get('total', 0):.2f}</p>
        """
        message = EmailMessage(
            to=email,
            subject=f"Order Confirmation #{order_number} - Octyrafiy",
            html_content=html_content,
        )
        return await self.send_email(message)

    async def send_shipping_notification(
        self,
        email: str,
        order_number: str,
        tracking_number: str | None,
        carrier: str | None,
    ) -> bool:
        """Send shipping notification email."""
        tracking_info = ""
        if tracking_number:
            tracking_info = f"""
            <p><strong>Tracking Number:</strong> {tracking_number}</p>
            <p><strong>Carrier:</strong> {carrier or 'N/A'}</p>
            """
        
        html_content = f"""
        <h1>Your Order Has Shipped!</h1>
        <p>Great news! Your order #{order_number} is on its way.</p>
        {tracking_info}
        <p>You can track your package using the tracking number above.</p>
        """
        message = EmailMessage(
            to=email,
            subject=f"Your Order #{order_number} Has Shipped! - Octyrafiy",
            html_content=html_content,
        )
        return await self.send_email(message)
