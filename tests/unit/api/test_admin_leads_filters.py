"""Admin lead filtering — the queries sales actually runs.

Two things are worth pinning down here. The first is that the joins are
LEFT: a lead whose tenant was deleted is precisely the lead this table
exists to preserve, and an inner join would hide it while looking
perfectly correct in every other respect.

The second is ``business_complete``, which is expressed twice — once as
``MerchantBusinessProfileModel.is_complete`` in Python and once as SQL in
the filter. Two expressions of one rule drift, so one test checks them
against each other over every combination rather than trusting that they
were written to match.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import and_, func, or_, select

from src.infrastructure.database.models.public.merchant_business_profile import (
    MerchantBusinessProfileModel,
)
from src.infrastructure.database.models.public.merchant_lead import MerchantLeadModel
from src.infrastructure.database.models.public.tenant import TenantModel


async def _tenant(session, plan="payg"):
    t = TenantModel(
        id=uuid4(),
        name="T",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state="active",
    )
    session.add(t)
    await session.flush()
    return t


async def _lead(session, *, tenant_id=None, **kw):
    lead = MerchantLeadModel(
        email=f"m-{uuid4().hex[:8]}@example.com",
        source="signup",
        status="registered",
        tenant_id=tenant_id,
        **kw,
    )
    session.add(lead)
    await session.flush()
    return lead


def _base_query():
    """The joined select the endpoint builds."""
    return (
        select(MerchantLeadModel, TenantModel, MerchantBusinessProfileModel)
        .outerjoin(TenantModel, TenantModel.id == MerchantLeadModel.tenant_id)
        .outerjoin(
            MerchantBusinessProfileModel,
            MerchantBusinessProfileModel.tenant_id == MerchantLeadModel.tenant_id,
        )
    )


def _complete_clause():
    """The SQL half of the completeness rule, as the endpoint writes it."""
    return (
        MerchantBusinessProfileModel.payout_encrypted.isnot(None),
        MerchantBusinessProfileModel.is_registered_business.isnot(None),
        or_(
            MerchantBusinessProfileModel.is_registered_business.is_(False),
            MerchantBusinessProfileModel.tax_id.isnot(None),
        ),
    )


@pytest.mark.asyncio
async def test_lead_without_a_tenant_still_appears(test_session):
    """The whole point of the leads table. An inner join would drop this."""
    await _lead(test_session, tenant_id=None, sells_what="fashion")
    await test_session.commit()

    rows = (await test_session.execute(_base_query())).all()

    assert len(rows) == 1
    lead, tenant, profile = rows[0]
    assert tenant is None and profile is None
    assert lead.sells_what == "fashion"


@pytest.mark.asyncio
async def test_filters_by_merchant_type_and_real_plan(test_session):
    """Merchant type comes off the lead, plan off the tenant."""
    fashion = await _tenant(test_session, plan="payg")
    electronics = await _tenant(test_session, plan="starter")
    await _lead(test_session, tenant_id=fashion.id, sells_what="fashion")
    await _lead(test_session, tenant_id=electronics.id, sells_what="electronics")
    await test_session.commit()

    rows = (
        await test_session.execute(
            _base_query().where(
                MerchantLeadModel.sells_what == "fashion",
                TenantModel.plan == "payg",
            )
        )
    ).all()

    assert len(rows) == 1
    assert rows[0][0].sells_what == "fashion"
    assert rows[0][1].plan == "payg"


@pytest.mark.asyncio
async def test_plan_intent_and_actual_plan_are_different_filters(test_session):
    """A merchant who clicked payg and landed on starter is findable."""
    t = await _tenant(test_session, plan="starter")
    await _lead(test_session, tenant_id=t.id, plan_intent="payg")
    await test_session.commit()

    diverged = (
        await test_session.execute(
            _base_query().where(
                MerchantLeadModel.plan_intent == "payg",
                TenantModel.plan != "payg",
            )
        )
    ).all()

    assert len(diverged) == 1


@pytest.mark.asyncio
async def test_business_complete_sql_matches_the_python_property(test_session):
    """The filter and the model property must agree on every combination.

    They are two encodings of one rule. If someone changes ``is_complete``
    and not the SQL, admin quietly shows a different set of merchants than
    the merchant's own hub does.
    """
    combos = list(
        itertools.product(
            [None, True, False],  # is_registered_business
            [None, "TAX123"],  # tax_id
            [None, b"cipher"],  # payout_encrypted
        )
    )
    for registered, tax_id, payout in combos:
        t = await _tenant(test_session)
        await _lead(test_session, tenant_id=t.id)
        test_session.add(
            MerchantBusinessProfileModel(
                tenant_id=t.id,
                is_registered_business=registered,
                tax_id=tax_id,
                payout_encrypted=payout,
                payout_key_id="k1" if payout else None,
            )
        )
    await test_session.commit()

    sql_complete = {
        row[2].tenant_id
        for row in (
            await test_session.execute(_base_query().where(*_complete_clause()))
        ).all()
    }

    all_profiles = (
        (await test_session.execute(select(MerchantBusinessProfileModel)))
        .scalars()
        .all()
    )
    python_complete = {p.tenant_id for p in all_profiles if p.is_complete}

    assert sql_complete == python_complete
    # Sanity: the grid must produce both outcomes, or the assertion above
    # passes trivially on two empty sets.
    assert 0 < len(python_complete) < len(all_profiles)


@pytest.mark.asyncio
async def test_incomplete_filter_includes_merchants_with_no_profile(test_session):
    """Not-ready has to mean never-answered too, not only answered-badly."""
    t = await _tenant(test_session)
    await _lead(test_session, tenant_id=t.id)  # no profile row at all
    await test_session.commit()

    rows = (
        await test_session.execute(
            _base_query().where(
                or_(
                    MerchantBusinessProfileModel.tenant_id.is_(None),
                    MerchantBusinessProfileModel.payout_encrypted.is_(None),
                    MerchantBusinessProfileModel.is_registered_business.is_(None),
                    and_(
                        MerchantBusinessProfileModel.is_registered_business.is_(True),
                        MerchantBusinessProfileModel.tax_id.is_(None),
                    ),
                )
            )
        )
    ).all()

    assert len(rows) == 1


@pytest.mark.asyncio
async def test_funnel_counts_are_monotonic(test_session):
    """Each step counts leads that ever reached it, so counts only fall."""
    now = datetime.now(UTC)
    t = await _tenant(test_session)
    await _lead(
        test_session,
        tenant_id=t.id,
        registered_at=now,
        store_created_at=now,
        first_product_at=now,
        first_order_at=now,
        first_commission_at=now,
    )
    await _lead(test_session, registered_at=now, store_created_at=now)
    await _lead(test_session, registered_at=now)
    await test_session.commit()

    row = (
        await test_session.execute(
            select(
                func.count(),
                func.count(MerchantLeadModel.registered_at),
                func.count(MerchantLeadModel.store_created_at),
                func.count(MerchantLeadModel.first_product_at),
                func.count(MerchantLeadModel.first_order_at),
                func.count(MerchantLeadModel.first_commission_at),
            ).select_from(MerchantLeadModel)
        )
    ).one()

    counts = [int(c) for c in row]
    assert counts == [3, 3, 2, 1, 1, 1]
    assert counts == sorted(counts, reverse=True)
