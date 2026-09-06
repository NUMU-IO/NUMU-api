"""Recording merchant leads — the single write path into ``merchant_leads``.

Both front doors call :func:`record_lead`: the demo modal via
``StartDemoUseCase`` and the pricing/signup modal via the register route.
Store creation calls it again to attach the tenant.

Two rules govern everything here:

**Never break a signup.** A lead row is worth a great deal to us and
nothing at all to the merchant standing at the form. Every failure path
swallows, logs and returns ``None``. The caller is expected to ignore the
return value.

**Never overwrite something with nothing.** A person who tried the demo
(name + WhatsApp, no password) and later signs up properly (name + email,
no phone, because the signup form asks for less) must not lose their
phone number to the second touch. Field merging is strictly additive
except for the handful of fields that legitimately change — see
:func:`_merge`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel

logger = logging.getLogger(__name__)

# Column widths from the model. Values are truncated to fit rather than
# rejected — a clipped referrer is worth more than a dropped lead.
_LIMITS = {
    "email": 255,
    "name": 160,
    "phone": 20,
    "whatsapp_phone": 20,
    "language": 5,
    "source": 32,
    "plan_intent": 20,
    "utm_source": 120,
    "utm_medium": 120,
    "utm_campaign": 120,
    "utm_content": 120,
    "referrer": 500,
    "landing_path": 255,
    "store_subdomain": 63,
    "sells_what": 32,
    "sells_where_today": 32,
    "monthly_orders_band": 20,
    "city": 80,
}


@dataclass(frozen=True)
class Attribution:
    """Where a lead came from, as reported by the landing page.

    All fields are visitor-controlled strings — they are truncated on
    write and never interpolated into anything. Missing is the norm:
    direct traffic carries no UTMs at all.
    """

    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    referrer: str | None = None
    landing_path: str | None = None

    def is_empty(self) -> bool:
        return not any((
            self.utm_source,
            self.utm_medium,
            self.utm_campaign,
            self.utm_content,
            self.referrer,
            self.landing_path,
        ))


def _clip(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[: _LIMITS.get(field, 255)]


def _merge(lead: MerchantLeadModel, field: str, value) -> None:
    """Set *field* only when it adds information.

    Blank incoming values never clear a stored one. This is what keeps a
    demo lead's WhatsApp number alive through a later signup that never
    asked for a phone.
    """
    if value is None or value == "":
        return
    if getattr(lead, field, None) in (None, ""):
        setattr(lead, field, value)


async def record_lead(
    db: AsyncSession,
    *,
    email: str,
    source: str,
    name: str | None = None,
    phone: str | None = None,
    whatsapp_phone: str | None = None,
    language: str | None = None,
    plan_intent: str | None = None,
    attribution: Attribution | None = None,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    store_subdomain: str | None = None,
    status: str | None = None,
    demo_started_at: datetime | None = None,
    registered_at: datetime | None = None,
    store_created_at: datetime | None = None,
) -> MerchantLeadModel | None:
    """Create or update the lead for *email*. Returns ``None`` on any failure.

    Does not commit — the row joins whatever transaction the caller is
    already in, so a signup that rolls back does not leave a lead behind
    for an account that does not exist.
    """
    normalized = _clip("email", (email or "").lower())
    if not normalized:
        return None

    try:
        lead = (
            await db.execute(
                select(MerchantLeadModel).where(
                    func.lower(MerchantLeadModel.email) == normalized
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(UTC)

        if lead is None:
            lead = MerchantLeadModel(
                email=normalized,
                source=_clip("source", source) or "signup",
                status="new",
            )
            # Inside a savepoint: a concurrent request for the same email
            # would otherwise raise on flush and poison the outer
            # transaction — taking the signup down with it.
            try:
                async with db.begin_nested():
                    db.add(lead)
                    await db.flush()
            except IntegrityError:
                lead = (
                    await db.execute(
                        select(MerchantLeadModel).where(
                            func.lower(MerchantLeadModel.email) == normalized
                        )
                    )
                ).scalar_one_or_none()
                if lead is None:
                    return None

        # ── Additive fields ───────────────────────────────────────
        _merge(lead, "name", _clip("name", name))
        _merge(lead, "phone", _clip("phone", phone))
        _merge(lead, "whatsapp_phone", _clip("whatsapp_phone", whatsapp_phone))
        _merge(lead, "language", _clip("language", language))
        _merge(lead, "plan_intent", _clip("plan_intent", plan_intent))
        _merge(lead, "tenant_id", tenant_id)
        _merge(lead, "user_id", user_id)
        _merge(lead, "store_subdomain", _clip("store_subdomain", store_subdomain))
        _merge(lead, "demo_started_at", demo_started_at)
        _merge(lead, "registered_at", registered_at)
        _merge(lead, "store_created_at", store_created_at)

        # First-touch attribution wins. A merchant who arrives from a
        # TikTok ad, leaves, and returns via a Google search three days
        # later was bought by the ad — overwriting on the second touch
        # would credit the channel that merely finished the job.
        if attribution is not None and not attribution.is_empty():
            _merge(lead, "utm_source", _clip("utm_source", attribution.utm_source))
            _merge(lead, "utm_medium", _clip("utm_medium", attribution.utm_medium))
            _merge(
                lead, "utm_campaign", _clip("utm_campaign", attribution.utm_campaign)
            )
            _merge(lead, "utm_content", _clip("utm_content", attribution.utm_content))
            _merge(lead, "referrer", _clip("referrer", attribution.referrer))
            _merge(
                lead, "landing_path", _clip("landing_path", attribution.landing_path)
            )

        # ── Fields that legitimately change ───────────────────────
        lead.last_source = _clip("source", source)
        lead.last_seen_at = now
        if status:
            lead.advance_status(status)

        await db.flush()
        return lead

    except Exception:
        # A lead is never worth a 500 on the signup path.
        logger.warning(
            "merchant_lead_record_failed", extra={"source": source}, exc_info=True
        )
        return None


async def attach_tenant_to_lead(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    subdomain: str | None,
) -> None:
    """Link the store a registered lead just created, by owner id.

    Called from store creation, where the email is not to hand but the
    owner id is. Silent when no lead exists — merchants created by an
    admin, or before this table shipped, simply have no lead row.
    """
    try:
        lead = (
            (
                await db.execute(
                    select(MerchantLeadModel).where(
                        MerchantLeadModel.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if lead is None:
            return
        _merge(lead, "tenant_id", tenant_id)
        _merge(lead, "store_subdomain", _clip("store_subdomain", subdomain))
        _merge(lead, "store_created_at", datetime.now(UTC))
        lead.advance_status("store_created")
        lead.last_seen_at = datetime.now(UTC)
        await db.flush()
    except Exception:
        logger.warning("merchant_lead_attach_failed", exc_info=True)


async def record_qualification(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    sells_what: str | None = None,
    sells_where_today: str | None = None,
    monthly_orders_band: str | None = None,
    city: str | None = None,
) -> None:
    """Store the onboarding wizard's qualification answers on the lead.

    Called from wizard configuration, where the tenant is known and the
    email is not. Silent when no lead exists, like every other writer
    here — a merchant created by an admin has no acquisition record and
    that is not an error.

    Unlike the rest of the merge, these fields **overwrite**. The wizard
    is re-runnable and a merchant who comes back to correct "I sell
    fashion" to "I sell electronics" means it; keeping the first answer
    would preserve a mistake on the grounds that it arrived first. The
    first-touch rule protects attribution, which is a historical fact.
    This is a current one.
    """
    try:
        lead = (
            (
                await db.execute(
                    select(MerchantLeadModel).where(
                        MerchantLeadModel.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if lead is None:
            return

        for field, value in (
            ("sells_what", sells_what),
            ("sells_where_today", sells_where_today),
            ("monthly_orders_band", monthly_orders_band),
            ("city", city),
        ):
            clipped = _clip(field, value)
            # A blank answer to an optional question must not erase a
            # previous one — the wizard sends every field on every run.
            if clipped:
                setattr(lead, field, clipped)

        lead.last_seen_at = datetime.now(UTC)
        await db.flush()
    except Exception:
        logger.warning("merchant_lead_qualification_failed", exc_info=True)
