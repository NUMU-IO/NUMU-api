"""Email service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmailMessage:
    """Email message data."""

    to: str | list[str]
    subject: str
    html_content: str
    text_content: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    attachments: list[dict] | None = None


class IEmailService(ABC):
    """Email service interface."""

    @abstractmethod
    async def send_email(self, message: EmailMessage) -> bool:
        """Send an email."""
        ...

    @abstractmethod
    async def send_verification_email(self, email: str, token: str) -> bool:
        """Send email verification email."""
        ...

    @abstractmethod
    async def send_password_reset_email(self, email: str, token: str) -> bool:
        """Send password reset email."""
        ...

    @abstractmethod
    async def send_order_confirmation(
        self,
        email: str,
        order_number: str,
        order_details: dict,
    ) -> bool:
        """Send order confirmation email."""
        ...

    @abstractmethod
    async def send_shipping_notification(
        self,
        email: str,
        order_number: str,
        tracking_number: str | None,
        carrier: str | None,
    ) -> bool:
        """Send shipping notification email."""
        ...
