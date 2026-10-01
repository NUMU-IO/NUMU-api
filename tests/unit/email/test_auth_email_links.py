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
