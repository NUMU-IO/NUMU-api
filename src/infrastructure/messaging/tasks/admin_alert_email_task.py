"""Email the platform operators when an admin queue gets new work.

Web push was the only channel, and it delivers to registered devices only —
an operator has to open the backoffice in a browser, grant permission, and
keep that registration alive. On production there were **zero** platform
device registrations, so every operator notification since the feature
shipped (new leads, payment proofs, WhatsApp access requests, theme
submissions) fanned out to nobody. Four leads arrived in one day and not one
of them was announced.

Email needs no registration, survives a cleared browser, and reaches a phone
that has never opened the backoffice. It sends alongside push rather than
replacing it: push is the faster channel when it is set up, and the two
deduplicate at the human, not in code.

Recipients come from `platform_settings.alert_emails`, editable in the admin
Settings screen, so adding a colleague never needs a deploy.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

# The backoffice origin. Notifications carry a relative admin path (the push
# service worker resolves it against its own origin); an email has no origin
# to resolve against, so the link has to be absolute.
ADMIN_ORIGIN = "https://admin.numueg.app"


def _html(title: str, body: str, url: str) -> str:
    link = f"{ADMIN_ORIGIN}{url if url.startswith('/') else '/' + url}"
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;color:#1a1a1a">'
        f'<h2 style="margin:0 0 8px;font-size:18px">{escape(title)}</h2>'
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.5">{escape(body)}</p>'
        f'<a href="{escape(link)}" style="display:inline-block;padding:10px 16px;'
        "background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px;"
        'font-size:14px">Open the backoffice</a>'
        "</div>"
    )


async def _send(*, title: str, body: str, url: str, important: bool) -> dict:
    from src.api.v1.routes.admin.platform_settings import get_platform_settings
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    async with AsyncSessionLocal() as session:
        settings = await get_platform_settings(session)

    recipients = [str(e).strip() for e in (settings.get("alert_emails") or []) if e]
    if not recipients:
        # Deliberate: clearing the list in Settings is how an operator turns
        # these off, and that must be silent rather than an error every time.
        return {"status": "skipped", "reason": "no_recipients"}

    sent = await ResendEmailService().send_email(
        EmailMessage(
            to=recipients,
            subject=f"{'[!] ' if important else ''}{title}",
            html_content=_html(title, body, url),
        )
    )
    return {"status": "sent" if sent else "failed", "recipients": len(recipients)}


@celery_app.task(
    name="tasks.send_admin_alert_email",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def send_admin_alert_email_task(
    self,
    title: str,
    body: str,
    url: str,
    important: bool = False,
) -> dict:
    try:
        return asyncio.run(_send(title=title, body=body, url=url, important=important))
    except Exception as exc:  # noqa: BLE001 — retry transport failures
        logger.warning("admin_alert_email_failed error=%s", exc)
        raise self.retry(exc=exc) from exc


def email_admins(*, title: str, body: str, url: str, important: bool = False) -> None:
    """Enqueue an operator email. NEVER raises.

    Same contract as `push_tasks.notify_admins`: every caller is a merchant
    action that has already committed, so a broker outage must cost the
    notification and nothing else.
    """
    try:
        send_admin_alert_email_task.delay(
            title=title, body=body, url=url, important=important
        )
    except Exception as exc:  # noqa: BLE001 — never break the producer
        logger.warning("admin_alert_email_enqueue_failed error=%s", exc)
