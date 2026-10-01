import re

import pytest

from src.config import settings
from src.infrastructure.external_services.resend.email_service import (
    ResendEmailService,
)


@pytest.mark.parametrize(
    ("send", "path"),
    [
        (lambda s: s.send_password_reset_email("m@x.com", "tok"), "/reset-password"),
        (
            lambda s: s.send_verification_email("m@x.com", "tok", "123456"),
            "/verify-email",
        ),
    ],
)
async def test_auth_email_links_point_at_merchant_hub(monkeypatch, send, path):
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    monkeypatch.setattr(settings, "merchant_hub_url", "https://merchant.numueg.app/")
    sent = []
    service = ResendEmailService(api_key="test")

    async def capture(message):
        sent.append(message)
        return True

    monkeypatch.setattr(service, "send_email", capture)
    await send(service)

    links = re.findall(r'href="([^"]+)"', sent[0].html_content)
    assert any(
        link.startswith(f"https://merchant.numueg.app{path}?token=tok")
        for link in links
    )
    assert not any("localhost" in link for link in links)


async def test_english_merchant_gets_english_auth_emails(monkeypatch):
    sent = []
    service = ResendEmailService(api_key="test")

    async def capture(message):
        sent.append(message)
        return True

    monkeypatch.setattr(service, "send_email", capture)
    await service.send_verification_email("m@x.com", "tok", "123456", language="en")
    await service.send_password_reset_email("m@x.com", "tok", language="en")

    verify, reset = sent
    assert verify.subject == "Confirm your email on numu"
    assert reset.subject == "Reset your password — numu"
    assert "&lang=en" in reset.html_content
    for message in sent:
        assert not re.search(r"[\u0600-\u06FF]", message.subject)
        assert 'dir="rtl"' not in message.html_content
