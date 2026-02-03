"""Email templates for configuration notifications.

This module provides structured email templates for:
- Configuration request notifications
- Credential configuration notifications
- Status update notifications
"""

from dataclasses import dataclass


@dataclass
class EmailTemplate:
    """Base email template."""
    subject: str
    html_body: str
    text_body: str


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
        """Generate email for new configuration request (to admin).

        Args:
            merchant_name: The merchant's business name
            service_name: The service being requested
            service_type: Type of service
            priority: Request priority
            notes: Optional merchant notes
            action_url: URL to view the request

        Returns:
            EmailTemplate with subject and body
        """
        subject = f"[NUMU] New Configuration Request: {service_name} - {priority.upper()}"

        notes_section = f"<p><strong>Merchant Notes:</strong> {notes}</p>" if notes else ""

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #1034A6; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background: #f5f5f5; }}
                .details {{ background: white; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                .priority-urgent {{ color: #dc3545; font-weight: bold; }}
                .priority-high {{ color: #fd7e14; font-weight: bold; }}
                .priority-normal {{ color: #28a745; }}
                .button {{ display: inline-block; padding: 12px 24px; background: #D4AF37; color: white; text-decoration: none; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>NUMU Admin</h1>
                    <p>New Configuration Request</p>
                </div>
                <div class="content">
                    <p>A merchant has requested service configuration:</p>

                    <div class="details">
                        <p><strong>Merchant:</strong> {merchant_name}</p>
                        <p><strong>Service:</strong> {service_name}</p>
                        <p><strong>Type:</strong> {service_type}</p>
                        <p><strong>Priority:</strong> <span class="priority-{priority.lower()}">{priority.upper()}</span></p>
                        {notes_section}
                    </div>

                    <p>Please review and configure the credentials:</p>
                    <a href="{action_url}" class="button">Configure Credentials</a>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
NUMU Admin - New Configuration Request

A merchant has requested service configuration:

Merchant: {merchant_name}
Service: {service_name}
Type: {service_type}
Priority: {priority.upper()}
{f"Notes: {notes}" if notes else ""}

Please review and configure the credentials at:
{action_url}
        """

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)

    @staticmethod
    def request_received_merchant(
        merchant_name: str,
        service_name: str,
        request_id: str,
    ) -> EmailTemplate:
        """Generate confirmation email for merchant.

        Args:
            merchant_name: The merchant's business name
            service_name: The service being requested
            request_id: The request ID for reference

        Returns:
            EmailTemplate with subject and body
        """
        subject = f"[NUMU] Configuration Request Received: {service_name}"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #D4AF37, #1034A6); color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; }}
                .reference {{ background: #f5f5f5; padding: 10px; border-radius: 5px; font-family: monospace; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>NUMU</h1>
                </div>
                <div class="content">
                    <p>Hello {merchant_name},</p>

                    <p>We've received your configuration request for <strong>{service_name}</strong>.</p>

                    <p>Our team will review your request and configure the service for your store. This typically takes 1-2 business days.</p>

                    <p>Reference ID:</p>
                    <div class="reference">{request_id}</div>

                    <p>You'll receive an email notification once the configuration is complete.</p>

                    <p>Thank you for choosing NUMU!</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
Hello {merchant_name},

We've received your configuration request for {service_name}.

Our team will review your request and configure the service for your store. This typically takes 1-2 business days.

Reference ID: {request_id}

You'll receive an email notification once the configuration is complete.

Thank you for choosing NUMU!
        """

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)


class CredentialsConfiguredEmailTemplate:
    """Email templates for credential configuration notifications."""

    @staticmethod
    def credentials_ready(
        merchant_name: str,
        service_name: str,
        service_type: str,
        features: list[str],
        action_url: str,
    ) -> EmailTemplate:
        """Generate email when credentials are configured.

        Args:
            merchant_name: The merchant's business name
            service_name: The service configured
            service_type: Type of service
            features: List of enabled features
            action_url: URL to access the service

        Returns:
            EmailTemplate with subject and body
        """
        subject = f"[NUMU] {service_name} is Now Active! 🎉"

        features_html = "".join([f"<li>{f}</li>" for f in features])

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #D4AF37, #1034A6); color: white; padding: 30px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 28px; }}
                .content {{ padding: 30px; }}
                .success-badge {{ background: #28a745; color: white; padding: 5px 15px; border-radius: 20px; display: inline-block; }}
                .features {{ background: #f5f5f5; padding: 20px; border-radius: 10px; margin: 20px 0; }}
                .button {{ display: inline-block; padding: 15px 30px; background: #D4AF37; color: white; text-decoration: none; border-radius: 5px; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎉 Great News!</h1>
                    <p>{service_name} is Ready</p>
                </div>
                <div class="content">
                    <p>Hello {merchant_name},</p>

                    <p><span class="success-badge">✓ ACTIVE</span></p>

                    <p>Your <strong>{service_name}</strong> integration has been configured and is ready to use!</p>

                    <div class="features">
                        <h3>What's Enabled:</h3>
                        <ul>
                            {features_html}
                        </ul>
                    </div>

                    <p>Start using {service_name} in your store:</p>
                    <a href="{action_url}" class="button">Go to Settings</a>

                    <p style="margin-top: 30px;">Need help? Our support team is here for you.</p>
                </div>
            </div>
        </body>
        </html>
        """

        features_text = "\n".join([f"  - {f}" for f in features])

        text_body = f"""
Great News! {service_name} is Ready

Hello {merchant_name},

Your {service_name} integration has been configured and is ready to use!

What's Enabled:
{features_text}

Start using {service_name} in your store:
{action_url}

Need help? Our support team is here for you.
        """

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)

    @staticmethod
    def credentials_revoked(
        merchant_name: str,
        service_name: str,
        reason: str,
    ) -> EmailTemplate:
        """Generate email when credentials are revoked.

        Args:
            merchant_name: The merchant's business name
            service_name: The service revoked
            reason: Reason for revocation

        Returns:
            EmailTemplate with subject and body
        """
        subject = f"[NUMU] Important: {service_name} Configuration Update"

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #dc3545; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; }}
                .alert {{ background: #fff3cd; border: 1px solid #ffc107; padding: 15px; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Configuration Update</h1>
                </div>
                <div class="content">
                    <p>Hello {merchant_name},</p>

                    <div class="alert">
                        <p><strong>Important:</strong> Your {service_name} configuration has been deactivated.</p>
                        <p><strong>Reason:</strong> {reason}</p>
                    </div>

                    <p>If you need to re-enable this service, please submit a new configuration request through your dashboard.</p>

                    <p>If you have questions, please contact our support team.</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
Configuration Update

Hello {merchant_name},

Important: Your {service_name} configuration has been deactivated.

Reason: {reason}

If you need to re-enable this service, please submit a new configuration request through your dashboard.

If you have questions, please contact our support team.
        """

        return EmailTemplate(subject=subject, html_body=html_body, text_body=text_body)
