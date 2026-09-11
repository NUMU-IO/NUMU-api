"""Merchant lead recording — the acquisition record that outlives a tenant.

The behaviours worth pinning down are the ones that quietly destroy data
if they regress: a second touch overwriting a first with blanks, a funnel
status moving backwards, and a duplicate email taking a signup down with
it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.application.services.merchant_leads import (
    Attribution,
    attach_tenant_to_lead,
    record_lead,
    record_qualification,
)
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel


@pytest.mark.asyncio
async def test_records_a_new_lead_with_attribution(test_session):
    lead = await record_lead(
        test_session,
        email="Sara@Example.com",
        source="demo",
        name="Sara Hassan",
        phone="+201001234567",
        language="ar",
        attribution=Attribution(
            utm_source="tiktok",
            utm_medium="paid",
            utm_campaign="cod-aug",
            landing_path="/pricing",
        ),
        status="demo_started",
        demo_started_at=datetime.now(UTC),
    )

    assert lead is not None
    # Stored lowercase so the unique index is a real one-row-per-person
    # guarantee rather than one per capitalisation.
    assert lead.email == "sara@example.com"
    assert lead.utm_source == "tiktok"
    assert lead.status == "demo_started"


@pytest.mark.asyncio
async def test_second_touch_never_clears_the_first(test_session):
    """A demo lead's WhatsApp number must survive a later signup.

    The signup form asks for less than the demo form did, so a naive
    "latest write wins" merge silently deletes the only phone number we
    ever had for that merchant.
    """
    await record_lead(
        test_session,
        email="omar@example.com",
        source="demo",
        name="Omar",
        phone="+201007654321",
        attribution=Attribution(utm_source="tiktok", utm_campaign="cod-aug"),
        status="demo_started",
    )

    lead = await record_lead(
        test_session,
        email="omar@example.com",
        source="signup",
        name="Omar Adel",
        phone=None,
        plan_intent="payg",
        attribution=Attribution(utm_source="google"),
        status="registered",
    )

    assert lead is not None
    assert lead.phone == "+201007654321"  # not cleared by the empty second touch
    assert lead.name == "Omar"  # first name kept; not overwritten
    assert lead.plan_intent == "payg"  # genuinely new information is added
    # First-touch attribution wins: the ad bought this merchant, the
    # later search merely finished the job.
    assert lead.utm_source == "tiktok"
    assert lead.last_source == "signup"
    assert lead.status == "registered"


@pytest.mark.asyncio
async def test_one_row_per_email(test_session):
    for source in ("demo", "signup", "signup"):
        await record_lead(
            test_session, email="dup@example.com", source=source, name="Dup"
        )

    rows = (await test_session.execute(MerchantLeadModel.__table__.select())).all()
    assert len([r for r in rows if r.email == "dup@example.com"]) == 1


@pytest.mark.asyncio
async def test_status_only_moves_forward(test_session):
    await record_lead(
        test_session, email="back@example.com", source="signup", status="store_created"
    )
    lead = await record_lead(
        test_session, email="back@example.com", source="demo", status="demo_started"
    )

    assert lead is not None
    # Re-entering the demo modal must not demote a merchant who already
    # has a store: the funnel column is the furthest point reached.
    assert lead.status == "store_created"


@pytest.mark.asyncio
async def test_blank_email_is_ignored_not_raised(test_session):
    assert await record_lead(test_session, email="", source="signup") is None


@pytest.mark.asyncio
async def test_attach_tenant_links_the_store(test_session):
    user_id = uuid4()
    tenant_id = uuid4()
    await record_lead(
        test_session,
        email="link@example.com",
        source="signup",
        user_id=user_id,
        status="registered",
    )

    await attach_tenant_to_lead(
        test_session,
        user_id=user_id,
        tenant_id=tenant_id,
        subdomain="linkstore",
        phone="+201001234567",
    )

    lead = (
        await test_session.execute(
            MerchantLeadModel.__table__.select().where(
                MerchantLeadModel.__table__.c.user_id == user_id
            )
        )
    ).first()
    assert lead is not None
    assert lead.tenant_id == tenant_id
    assert lead.store_subdomain == "linkstore"
    assert lead.phone == "+201001234567"
    assert lead.status == "store_created"


@pytest.mark.asyncio
async def test_attach_tenant_is_silent_for_unknown_owner(test_session):
    # Merchants created by an admin, or before this table shipped, have
    # no lead row. Store creation must not care.
    await attach_tenant_to_lead(
        test_session, user_id=uuid4(), tenant_id=uuid4(), subdomain="ghost"
    )


# ── Qualification ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qualification_is_recorded_against_the_tenant(test_session):
    tenant_id = uuid4()
    await record_lead(
        test_session,
        email="omar@example.com",
        source="signup",
        tenant_id=tenant_id,
        status="store_created",
    )

    await record_qualification(
        test_session,
        tenant_id=tenant_id,
        sells_what="fashion",
        sells_where_today="instagram",
        monthly_orders_band="51-200",
        city="Mansoura",
    )

    lead = (
        await test_session.execute(
            select(MerchantLeadModel).where(MerchantLeadModel.tenant_id == tenant_id)
        )
    ).scalar_one()
    assert lead.sells_what == "fashion"
    assert lead.sells_where_today == "instagram"
    assert lead.monthly_orders_band == "51-200"
    assert lead.city == "Mansoura"


@pytest.mark.asyncio
async def test_rerunning_the_wizard_corrects_an_answer(test_session):
    """Qualification overwrites — unlike attribution, which is first-touch.

    A merchant who re-runs the wizard and changes what they sell is
    correcting a fact about today, not rewriting where they came from.
    """
    tenant_id = uuid4()
    await record_lead(
        test_session, email="nour@example.com", source="signup", tenant_id=tenant_id
    )
    await record_qualification(
        test_session, tenant_id=tenant_id, sells_what="fashion", city="Cairo"
    )

    await record_qualification(
        test_session, tenant_id=tenant_id, sells_what="electronics"
    )

    lead = (
        await test_session.execute(
            select(MerchantLeadModel).where(MerchantLeadModel.tenant_id == tenant_id)
        )
    ).scalar_one()
    assert lead.sells_what == "electronics"
    # The wizard posts every field every run; a question left blank must
    # not wipe the answer given last time.
    assert lead.city == "Cairo"


@pytest.mark.asyncio
async def test_qualification_for_an_unknown_tenant_is_silent(test_session):
    """Admin-created merchants have no lead row. Not an error."""
    await record_qualification(test_session, tenant_id=uuid4(), sells_what="beauty")
