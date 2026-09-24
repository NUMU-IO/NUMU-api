"""The partner portal's notification feed: the one write path.

Every partner notification goes through ``emit_partner_notification`` (one
partner) or ``post_platform_notice`` (every approved partner), inside the
caller's transaction. The portal renders the copy from ``kind`` + ``data``,
in the reader's language.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerNotificationModel,
)

#: review_status {app_id, app_name, subject, version, status};
#: payout_recorded {amount_cents, currency, reference};
#: platform_notice {notice_id, notice_kind, title, body, link};
#: subscription_past_due {app_id, app_name} (no emitter yet).
PARTNER_NOTIFICATION_KINDS = frozenset({
    "review_status",
    "payout_recorded",
    "platform_notice",
    "subscription_past_due",
})


async def emit_partner_notification(
    db: AsyncSession,
    *,
    partner_id: UUID,
    kind: str,
    data: dict[str, Any],
    app_id: UUID | None = None,
    link: str | None = None,
) -> PartnerNotificationModel:
    if kind not in PARTNER_NOTIFICATION_KINDS:
        raise ValueError(f"unknown partner notification kind: {kind}")
    row = PartnerNotificationModel(
        id=uuid4(),
        partner_id=partner_id,
        kind=kind,
        data=data,
        app_id=app_id,
        link=link,
    )
    db.add(row)
    await db.flush()
    return row


async def post_platform_notice(
    db: AsyncSession,
    *,
    notice_kind: str,
    title: dict[str, str],
    body: dict[str, str],
    link: str | None,
) -> tuple[UUID, int]:
    """A changelog or deprecation notice to every approved partner."""
    notice_id = uuid4()
    partner_ids = (
        await db.scalars(
            select(PartnerAccountModel.id).where(
                PartnerAccountModel.status == "approved"
            )
        )
    ).all()
    data = {
        "notice_id": str(notice_id),
        "notice_kind": notice_kind,
        "title": title,
        "body": body,
        "link": link,
    }
    db.add_all(
        PartnerNotificationModel(
            id=uuid4(), partner_id=pid, kind="platform_notice", data=data, link=link
        )
        for pid in partner_ids
    )
    await db.flush()
    return notice_id, len(partner_ids)
