"""Canary / load-test recipients never reach Resend; real recipients still do."""

from src.core.interfaces.services.email_service import EmailMessage
from src.infrastructure.external_services.resend import email_service
from src.infrastructure.external_services.resend.email_service import (
    ResendEmailService,
)


async def test_test_domains_are_skipped_and_real_addresses_kept(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service.resend.Emails, "send", sent.append)
    service = ResendEmailService(api_key="re_test")

    assert await service.send_email(
        EmailMessage(to="canary-checkout@numu-test.io", subject="s", html_content="h")
    )
    assert await service.send_email(
        EmailMessage(to=["hello@NUMUEG.tech"], subject="s", html_content="h")
    )
    assert sent == []

    await service.send_email(
        EmailMessage(
            to=["owner@store.com", "load+1@numu-test.io"], subject="s", html_content="h"
        )
    )
    assert [p["to"] for p in sent] == [["owner@store.com"]]
