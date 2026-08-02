"""Celery tasks for InstaPay subscription payments + lifecycle warnings.

* ``tasks.expire_subscription_payment_intents`` — beat, every 15 min
  (offset from the wallet sweep): ``awaiting_proof`` intents past
  ``expires_at`` → ``expired``. ``under_review`` intents are NEVER
  expired — a human owes the merchant a decision on the uploaded
  receipt (same contract as ``wallet_topup_expiry_task``).

* ``tasks.send_renewal_payment_due`` — bilingual dunning nudge enqueued
  by the renewal sweep when a charge failed / no funding source covered
  the period. Points the merchant at /billing: pay by card, top up the
  wallet, or pay via InstaPay.

* ``tasks.send_subscription_expiry_warnings`` — hourly beat: bilingual
  heads-up emails BEFORE anything happens — trial tenants N days before
  ``expires_at``, paid tenants N days before ``next_renewal_at``. The
  windows and a master switch are admin-tunable via
  ``billing_lifecycle_settings`` (see admin/subscription-payments
  /settings). Dedup via ``tenants.renewal_warning_sent_at``: warned
  inside THIS period's window → skip; the stamp re-arms automatically
  when the anchor advances to the next period.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.expire_subscription_payment_intents")
def expire_subscription_payment_intents_task() -> dict:
    return asyncio.run(_async_expire())


async def _async_expire() -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import update

    from src.core.entities.subscription_payment import (
        SubscriptionPaymentIntentStatus,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.subscription_payment import (
        SubscriptionPaymentIntentModel,
    )

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(SubscriptionPaymentIntentModel)
            .where(
                SubscriptionPaymentIntentModel.status
                == SubscriptionPaymentIntentStatus.AWAITING_PROOF.value,
                SubscriptionPaymentIntentModel.expires_at.isnot(None),
                SubscriptionPaymentIntentModel.expires_at < now,
            )
            .values(
                status=SubscriptionPaymentIntentStatus.EXPIRED.value,
                failure_reason="expired",
            )
        )
        await session.commit()
        expired = result.rowcount or 0

    if expired:
        logger.info("subscription_payments_expired", extra={"count": expired})
    return {"expired": expired}


@celery_app.task(name="tasks.send_subscription_expiry_warnings")
def send_subscription_expiry_warnings_task(batch_size: int = 200) -> dict:
    return asyncio.run(_async_send_expiry_warnings(batch_size))


async def _collect_warning_targets(session, cfg, now):  # noqa: ANN001
    """Tenants due a pre-expiry warning email right now.

    Returns ``[(tenant, kind, anchor)]`` where kind is ``"trial"`` or
    ``"renewal"`` and anchor is the datetime the warning is about.
    Selection is window-based with per-period dedup:

      inside window:  anchor - warning_days <= now < anchor
      not yet warned: renewal_warning_sent_at IS NULL
                      OR renewal_warning_sent_at < anchor - warning_days
                      (a stamp from a PREVIOUS period predates this
                      window's start, so it no-ops the dedup — re-armed)

    The window filter runs in SQL; the dedup comparison runs in Python
    over the ≤200 in-window candidates — column-minus-interval SQL
    isn't portable (SQLite tests), and the candidate set is tiny.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from src.infrastructure.database.models.public.tenant import TenantModel

    def _armed(sent, anchor, window):  # noqa: ANN001 — tiny local predicate
        if sent is None:
            return True
        # SQLite loads DateTime(timezone=True) back naive — normalize so
        # the comparison never mixes aware/naive.
        window_start = anchor - window
        if sent.tzinfo is None and window_start.tzinfo is not None:
            window_start = window_start.replace(tzinfo=None)
        elif sent.tzinfo is not None and window_start.tzinfo is None:
            sent = sent.replace(tzinfo=None)
        return sent < window_start

    targets = []

    renewal_window = timedelta(days=cfg.renewal_warning_days)
    renewal_rows = (
        (
            await session.execute(
                select(TenantModel)
                .where(
                    TenantModel.lifecycle_state == "active",
                    TenantModel.is_internal.is_(False),
                    TenantModel.next_renewal_at.isnot(None),
                    TenantModel.next_renewal_at > now,
                    TenantModel.next_renewal_at <= now + renewal_window,
                )
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    # GRANDFATHER GUARD (mirrors the renewal sweep): tenants with no
    # card token AND no completed InstaPay payment were never charged
    # before this rollout and won't be now — don't suddenly email
    # existing merchants about renewals that will never be collected.
    no_token_ids = [t.id for t in renewal_rows if not t.paymob_card_token_encrypted]
    instapay_payers: set = set()
    if no_token_ids:
        from src.infrastructure.database.models.public.subscription_payment import (
            SubscriptionPaymentIntentModel,
        )

        rows = (
            await session.execute(
                select(SubscriptionPaymentIntentModel.tenant_id)
                .where(
                    SubscriptionPaymentIntentModel.tenant_id.in_(no_token_ids),
                    SubscriptionPaymentIntentModel.status == "succeeded",
                )
                .distinct()
            )
        ).all()
        instapay_payers = {r[0] for r in rows}
    targets.extend(
        (t, "renewal", t.next_renewal_at)
        for t in renewal_rows
        if (t.paymob_card_token_encrypted or t.id in instapay_payers)
        and _armed(t.renewal_warning_sent_at, t.next_renewal_at, renewal_window)
    )

    trial_window = timedelta(days=cfg.trial_warning_days)
    trial_rows = (
        (
            await session.execute(
                select(TenantModel)
                .where(
                    TenantModel.lifecycle_state == "trial",
                    TenantModel.is_internal.is_(False),
                    TenantModel.expires_at.isnot(None),
                    TenantModel.expires_at > now,
                    TenantModel.expires_at <= now + trial_window,
                )
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    targets.extend(
        (t, "trial", t.expires_at)
        for t in trial_rows
        if _armed(t.renewal_warning_sent_at, t.expires_at, trial_window)
    )
    return targets


async def _async_send_expiry_warnings(batch_size: int) -> dict:
    from datetime import UTC, datetime

    from src.application.services.billing_settings import get_billing_settings
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.messaging.tasks.wallet_notification_tasks import (
        _owner_email,
    )

    now = datetime.now(UTC)
    stats = {"renewal": 0, "trial": 0, "skipped_no_email": 0}

    async with AsyncSessionLocal() as session:
        cfg = await get_billing_settings(session, use_cache=False)
        if not cfg.warning_emails_enabled:
            return {"status": "disabled"}

        targets = (await _collect_warning_targets(session, cfg, now))[:batch_size]
        if not targets:
            return {"status": "ok", **stats}

        mailer = ResendEmailService()
        for tenant, kind, anchor in targets:
            email = await _owner_email(session, str(tenant.id))
            if not email:
                # Stamp anyway — retrying hourly can't conjure an email.
                tenant.renewal_warning_sent_at = now
                stats["skipped_no_email"] += 1
                continue

            days_left = max(
                1, (anchor - now).days + (1 if (anchor - now).seconds else 0)
            )
            date_str = anchor.strftime("%Y-%m-%d")
            if kind == "trial":
                subject = "تجربتك المجانية على وشك الانتهاء | Your trial is ending soon"
                html = (
                    f"<div dir='rtl' style='font-family:sans-serif'>"
                    f"<p>تجربتك المجانية تنتهي خلال {days_left} يوم "
                    f"(بتاريخ {date_str}).</p>"
                    f"<p>اختر باقتك من صفحة الاشتراك في لوحة التحكم حتى لا "
                    f"يتوقف متجرك — الدفع متاح بالبطاقة أو إنستاباي.</p></div><hr>"
                    f"<div style='font-family:sans-serif'>"
                    f"<p>Your free trial ends in {days_left} day(s) "
                    f"(on {date_str}).</p>"
                    f"<p>Pick a plan from the Billing page so your store keeps "
                    f"running — pay by card or InstaPay.</p></div>"
                )
            else:
                subject = "اشتراكك سيتجدد قريباً | Your subscription renews soon"
                html = (
                    f"<div dir='rtl' style='font-family:sans-serif'>"
                    f"<p>اشتراكك يتجدد يوم {date_str} "
                    f"(خلال {days_left} يوم).</p>"
                    f"<p>تأكد من وسيلة الدفع: بطاقة محفوظة تُخصم تلقائياً، "
                    f"أو ادفع مبكراً عبر إنستاباي من صفحة الاشتراك.</p>"
                    f"</div><hr>"
                    f"<div style='font-family:sans-serif'>"
                    f"<p>Your subscription renews on {date_str} "
                    f"(in {days_left} day(s)).</p>"
                    f"<p>Make sure a payment source is ready: a saved card "
                    f"charges automatically, or pay early via InstaPay from "
                    f"the Billing page.</p></div>"
                )

            try:
                await mailer.send_email(
                    EmailMessage(to=email, subject=subject, html_content=html)
                )
            except Exception:  # noqa: BLE001 — one bad address can't kill the sweep
                logger.warning(
                    "subscription_warning_email_failed",
                    extra={"tenant_id": str(tenant.id), "kind": kind},
                )
                continue
            tenant.renewal_warning_sent_at = now
            stats[kind] += 1

        await session.commit()

    if stats["renewal"] or stats["trial"]:
        logger.info("subscription_expiry_warnings_sent", extra=stats)
    return {"status": "ok", **stats}


@celery_app.task(name="tasks.send_renewal_payment_due", max_retries=2)
def send_renewal_payment_due_task(
    *, tenant_id: str, amount_cents: int, retry_count: int
) -> dict:
    return asyncio.run(_async_send_renewal_due(tenant_id, amount_cents, retry_count))


async def _async_send_renewal_due(
    tenant_id: str, amount_cents: int, retry_count: int
) -> dict:
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.messaging.tasks.wallet_notification_tasks import (
        _egp,
        _owner_email,
    )

    async with AsyncSessionLocal() as session:
        email = await _owner_email(session, tenant_id)
    if not email:
        return {"status": "skipped", "reason": "no_owner_email"}

    amount = _egp(amount_cents)
    html = (
        f"<div dir='rtl' style='font-family:sans-serif'>"
        f"<p>لم نتمكن من تجديد اشتراكك ({amount}).</p>"
        f"<p>للتجديد: ادفع بالبطاقة أو عبر إنستاباي "
        f"من صفحة الاشتراك في لوحة التحكم.</p></div><hr>"
        f"<div style='font-family:sans-serif'>"
        f"<p>We couldn't renew your subscription ({amount}).</p>"
        f"<p>To renew: pay by card or via InstaPay "
        f"from the Billing page in your dashboard.</p></div>"
    )
    sent = await ResendEmailService().send_email(
        EmailMessage(
            to=email,
            subject="تجديد الاشتراك مطلوب | Subscription renewal due",
            html_content=html,
        )
    )
    logger.info(
        "renewal_payment_due_email",
        extra={
            "tenant_id": tenant_id,
            "retry_count": retry_count,
            "sent": bool(sent),
        },
    )
    return {"status": "sent" if sent else "failed"}
