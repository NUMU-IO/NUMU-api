"""Instant notifications for platform operators.

The admin backoffice is a queue-driven tool: WhatsApp access requests, payment
proofs, theme submissions and new leads all sit waiting for a human. Until now
the only way to learn one had arrived was to open the app and look, so the
response time on a merchant's blocked signup was however long it took someone
to check.

Every notification here is queue-shaped — it names work that exists and links
to the screen that clears it. Nothing is emitted for an event an operator
cannot act on.

DEFERRED TO COMMIT. Each caller is inside a merchant-facing write, and a
notification that fires before its transaction commits is either a lie (the
signup rolled back) or a race (the operator taps through before the row is
visible). `_after_commit` buffers on the request session and flushes from
SQLAlchemy's `after_commit`, discarding the buffer on rollback — the same
approach `infrastructure/events/deferred_dispatch.py` takes for domain events,
without needing a domain event for what is one push.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

logger = logging.getLogger(__name__)

_BUFFER_KEY = "_admin_push_buffer"
_LISTENER_FLAG = "_admin_push_listener_installed"


def _send(payload: dict) -> None:
    from src.infrastructure.messaging.tasks.push_tasks import notify_admins

    notify_admins(**payload)


def _drain(sync_session: SyncSession) -> list[dict]:
    return sync_session.info.pop(_BUFFER_KEY, [])


def _on_commit(sync_session: SyncSession) -> None:
    """Flush the buffer. Swallows everything, on purpose.

    This runs INSIDE `await session.commit()`. An exception here propagates
    out of the commit and fails a request whose data is already durable — the
    merchant would see a 500 for a top-up that went through. A broker outage
    must cost the notification and nothing else.
    """
    for payload in _drain(sync_session):
        try:
            _send(payload)
        except Exception as exc:  # noqa: BLE001 — never break the commit
            logger.warning(
                "admin_notify_send_failed tag=%s error=%s", payload.get("tag"), exc
            )


def _on_rollback(sync_session: SyncSession) -> None:
    _drain(sync_session)  # discard — the work did not persist


def _on_soft_rollback(sync_session: SyncSession, previous_transaction) -> None:
    _drain(sync_session)


def _after_commit(db: AsyncSession | None, payload: dict) -> None:
    """Queue one notification for after the caller's transaction commits.

    Sends immediately when there is no open transaction to hang off — a Celery
    task or a test, where the write is already durable.
    """
    if db is None or not db.in_transaction():
        _send(payload)
        return

    sync_session = db.sync_session
    sync_session.info.setdefault(_BUFFER_KEY, []).append(payload)

    if not sync_session.info.get(_LISTENER_FLAG):
        sync_session.info[_LISTENER_FLAG] = True
        sa_event.listen(sync_session, "after_commit", _on_commit)
        sa_event.listen(sync_session, "after_rollback", _on_rollback)
        sa_event.listen(sync_session, "after_soft_rollback", _on_soft_rollback)


def notify(
    db: AsyncSession | None,
    *,
    title: str,
    body: str,
    url: str,
    tag: str,
    important: bool = False,
) -> None:
    """Notify every platform operator, once the caller's write is durable.

    NEVER raises: the caller is always a merchant action that has already
    succeeded, and a broker being down must not turn a successful signup into
    a 500.

    `url` is a relative admin path. `tag` collapses duplicates at the OS level,
    which is what stops five proofs from one merchant stacking five
    notifications — pass a tag that identifies the QUEUE, not the row, when
    that is the behaviour you want.
    """
    try:
        _after_commit(
            db,
            {
                "title": title,
                "body": body,
                "url": url,
                "tag": tag,
                "important": important,
            },
        )
    except Exception as exc:  # noqa: BLE001 — never break the producer
        logger.warning("admin_notify_failed tag=%s error=%s", tag, exc)


# ── The events worth waking someone for ──────────────────────────────────────
#
# Bodies carry a store or merchant name and nothing else. These render on a
# lock screen, and an operator's phone is no more private than a merchant's:
# no customer names, no phones, no amounts beyond what the queue itself is
# about.


async def store_name_for_tenant(db: AsyncSession, tenant_id: UUID | str) -> str:
    """Best-effort display name for a tenant, for the notification body.

    A tenant can own more than one store; the oldest is the one an operator
    thinks of as "the merchant". Returns a generic label rather than raising —
    a missing name must not cost the notification.
    """
    try:
        row = (
            await db.execute(
                text(
                    "SELECT name FROM public.stores WHERE tenant_id = :t "
                    "ORDER BY created_at ASC LIMIT 1"
                ),
                {"t": str(tenant_id)},
            )
        ).first()
        return (row[0] if row and row[0] else None) or "A merchant"
    except Exception:  # noqa: BLE001 — a name is never worth failing a payment
        return "A merchant"


def whatsapp_access_requested(db: AsyncSession, *, store_name: str) -> None:
    notify(
        db,
        title="WhatsApp access requested",
        body=f"{store_name} is waiting for a decision.",
        url="/whatsapp-access",
        # Queue-level tag: ten requests in a morning are one thing to go and
        # look at, not ten separate interruptions.
        tag="admin:whatsapp-access",
    )


def wallet_topup_submitted(db: AsyncSession, *, store_name: str) -> None:
    notify(
        db,
        title="Wallet top-up proof",
        body=f"{store_name} submitted a transfer to verify.",
        url="/wallets",
        tag="admin:wallet-topups",
    )


def subscription_proof_submitted(db: AsyncSession, *, store_name: str) -> None:
    notify(
        db,
        title="Subscription payment proof",
        body=f"{store_name} submitted a transfer to verify.",
        url="/subscription-payments",
        tag="admin:subscription-payments",
        # A merchant who has paid is waiting on us to unlock their plan.
        important=True,
    )


def theme_submitted(db: AsyncSession | None, *, theme_name: str, version: str) -> None:
    notify(
        db,
        title="Theme submitted for review",
        body=f"{theme_name} {version} is queued for review.",
        url="/marketplace-reviews",
        tag="admin:marketplace-review",
    )


def lead_captured(db: AsyncSession, *, email: str, source: str) -> None:
    """A new merchant lead — the one notification that is about opportunity.

    Only ever fires for a lead row that did not exist. `record_lead` is called
    again on every later touch (demo, signup, store created) and re-notifying
    on those would turn one merchant's journey into four buzzes.

    The email IS the lead, so it is the body; there is nothing else to
    identify them by at first touch.
    """
    notify(
        db,
        title="New merchant lead",
        body=f"{email} — {source}",
        url="/leads",
        # Row-level tag: leads are individually actionable, and collapsing them
        # would hide the second one behind the first.
        tag=f"admin:lead:{email}",
    )
