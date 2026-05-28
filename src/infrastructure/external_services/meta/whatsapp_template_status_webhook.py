"""Handler for Meta's ``message_template_status_update`` webhook field
(FR-028 / US5).

Meta delivers template approval-status changes to the same webhook URL
as inbound messages, distinguished by ``entry[].changes[].field ==
"message_template_status_update"``. Payload shape (per research.md R1)::

    {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "<waba_id>",
            "changes": [{
                "field": "message_template_status_update",
                "value": {
                    "event": "APPROVED|REJECTED|FLAGGED|PAUSED|DISABLED",
                    "message_template_id": "<meta_template_id>",
                    "message_template_name": "string",
                    "message_template_language": "en|ar|...",
                    "reason": "ABUSIVE_CONTENT|INVALID_FORMAT|null",
                    "disable_info": { "disable_date": "string" }
                }
            }]
        }]
    }

Idempotency (TASK-SEC-008): re-applying the same status is a no-op
that does not produce extra DB writes or downstream notifications.
Meta retries webhook deliveries on non-2xx, so duplicate payloads
must NOT compound their effect.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)

logger = get_logger(__name__)


async def handle_template_status_update(
    db: AsyncSession,
    *,
    waba_id: str,
    value: dict[str, Any],
) -> bool:
    """Apply one ``value`` payload from a Meta webhook change.

    Returns True iff the local row was actually mutated; False on
    idempotent no-op (same status already applied) or row-not-found.
    Caller is responsible for the DB commit.
    """
    event = value.get("event")
    meta_template_id = value.get("message_template_id")
    template_name = value.get("message_template_name")
    template_language = value.get("message_template_language")
    reason = value.get("reason")

    if not event:
        logger.warning("template_status_webhook_no_event", waba_id=waba_id)
        return False

    # Resolve the store this WABA belongs to. Two paths:
    # 1. Platform WABA — multiple stores share it; we cannot resolve a
    #    single store from waba_id alone. The system templates are seeded
    #    per-store (one row per store), so we fan out the status change
    #    across every is_system row matching the (name, language) pair.
    # 2. BYO WABA — exactly one ServiceCredential row maps to it; we use
    #    its tenant_id to scope the lookup to that tenant's rows.
    store_id = await _resolve_store_from_waba(db, waba_id)

    # Locate the local template row(s). Sentry HIGH-3: previously this
    # was scalar_one_or_none() and threw MultipleResultsFound on every
    # platform-WABA webhook because system templates duplicate per-store.
    rows = await _find_template_rows(
        db,
        store_id=store_id,
        meta_template_id=meta_template_id,
        name=template_name,
        language=template_language,
    )
    if not rows:
        logger.info(
            "template_status_webhook_no_local_row",
            waba_id=waba_id,
            meta_template_id=meta_template_id,
            event=event,
        )
        return False

    new_status = _meta_event_to_status(event)
    mutated_any = False
    for row in rows:
        # IDEMPOTENCY (TASK-SEC-008): re-applying same status is a no-op.
        if row.status == new_status and (
            new_status != "REJECTED" or row.rejection_reason == reason
        ):
            logger.debug(
                "template_status_webhook_idempotent_skip",
                template_id=str(row.id),
                status=new_status,
            )
            continue

        row.status = new_status
        if new_status == "REJECTED":
            row.rejection_reason = reason
        elif new_status == "APPROVED" and row.approved_at is None:
            row.approved_at = datetime.now(UTC)
        mutated_any = True
        logger.info(
            "template_status_updated",
            template_id=str(row.id),
            meta_template_id=meta_template_id,
            new_status=new_status,
            source="webhook",
        )

    if mutated_any:
        await db.flush()
    return mutated_any


async def _resolve_store_from_waba(db: AsyncSession, waba_id: str) -> Any | None:
    """Return the store_id whose BYO credential maps to this waba_id, or
    None for platform WABA / unmapped.
    """
    cred = (
        (
            await db.execute(
                select(ServiceCredential).where(
                    ServiceCredential.service_type == ServiceType.WHATSAPP,
                    ServiceCredential.service_name == ServiceName.WHATSAPP_BUSINESS,
                    ServiceCredential.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for c in cred:
        meta = c.extra_metadata or {}
        if str(meta.get("waba_id") or "") == str(waba_id):
            # ServiceCredential is tenant-scoped, not store-scoped, but
            # extra_metadata identifies the phone/waba. The local
            # templates table has store_id + tenant_id; tenant_id from
            # the credential row narrows it.
            return c.tenant_id
    return None


async def _find_template_rows(
    db: AsyncSession,
    *,
    store_id: Any | None,
    meta_template_id: str | None,
    name: str | None,
    language: str | None,
) -> list[WhatsAppTemplateModel]:
    """Find local template row(s) matching this webhook event.

    Lookup precedence:

    1. ``meta_template_id`` — uniquely identifies the Meta-side template
       across the WABA, so when present we narrow to all local rows
       carrying that id (typically 1; could be N per-store copies of
       the same system template post-seeding).
    2. ``(store_id, name, language)`` fallback when meta_template_id is
       not yet recorded locally (template was just submitted and the
       backend hasn't stored its Meta id yet).

    Scoping rules:

    - BYO WABA (``store_id`` resolved from ServiceCredential.tenant_id):
      narrow by ``tenant_id == store_id`` to the BYO merchant's rows.
    - Platform WABA (``store_id is None``): the system templates are
      seeded per-store with ``is_system=true``. Fan out the status
      change to every is_system row matching the (name, language) — the
      Meta-side template approval applies to all of them.
    """
    if meta_template_id:
        rows = (
            (
                await db.execute(
                    select(WhatsAppTemplateModel).where(
                        WhatsAppTemplateModel.meta_template_id == meta_template_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if rows:
            return list(rows)

    if name and language:
        stmt = select(WhatsAppTemplateModel).where(
            WhatsAppTemplateModel.name == name,
            WhatsAppTemplateModel.language == language,
        )
        if store_id is not None:
            stmt = stmt.where(WhatsAppTemplateModel.tenant_id == store_id)
        else:
            # Platform WABA path — restrict to system templates so a BYO
            # merchant happening to have a same-named template doesn't get
            # its status accidentally flipped by a platform-side webhook.
            stmt = stmt.where(WhatsAppTemplateModel.is_system.is_(True))
        return list((await db.execute(stmt)).scalars().all())

    return []


def _meta_event_to_status(event: str) -> str:
    """Map Meta's webhook event value to our local status enum.

    Meta uses ``APPROVED`` / ``REJECTED`` / ``FLAGGED`` / ``PAUSED`` /
    ``DISABLED`` — we mirror these directly.
    """
    return event.upper() if event else "PENDING"
