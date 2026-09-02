"""Commercial details — encryption, partial writes, and completeness.

The two rules that matter: an account number must never be recoverable
from what we store in the clear, and a form that submits one section must
not blank another. The second sounds pedantic until a front-end bug sends
an empty string for an untouched field and wipes a merchant's tax id.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.business_profile import (
    get_profile,
    read_payout_account,
    upsert_profile,
)

ACCOUNT = "EG380019000500000000263180002"


@pytest.mark.asyncio
async def test_payout_number_is_encrypted_and_masked(test_session):
    tenant_id = uuid4()

    profile = await upsert_profile(
        test_session,
        tenant_id=tenant_id,
        payout_bank_name="CIB",
        payout_account_name="Sara Hassan",
        payout_account_number=ACCOUNT,
    )

    # The clear-text columns must not contain the number, in whole or part.
    assert profile.payout_encrypted is not None
    assert ACCOUNT.encode() not in profile.payout_encrypted
    assert ACCOUNT not in (profile.payout_masked or "")
    assert profile.payout_masked.endswith(ACCOUNT[-4:])
    assert profile.payout_key_id
    assert profile.has_payout_account is True


@pytest.mark.asyncio
async def test_payout_number_round_trips(test_session):
    """Encryption is only useful if there is a documented way back out."""
    tenant_id = uuid4()
    await upsert_profile(
        test_session, tenant_id=tenant_id, payout_account_number=ACCOUNT
    )

    assert await read_payout_account(test_session, tenant_id=tenant_id) == ACCOUNT


@pytest.mark.asyncio
async def test_partial_write_leaves_other_fields_alone(test_session):
    """Saving the payout section must not erase the tax section."""
    tenant_id = uuid4()
    await upsert_profile(
        test_session,
        tenant_id=tenant_id,
        is_registered_business=True,
        tax_id="TAX-123",
    )

    await upsert_profile(
        test_session, tenant_id=tenant_id, payout_account_number=ACCOUNT
    )

    profile = await get_profile(test_session, tenant_id=tenant_id)
    assert profile.tax_id == "TAX-123"
    assert profile.is_registered_business is True
    assert profile.has_payout_account is True


@pytest.mark.asyncio
async def test_unanswered_is_not_the_same_as_no(test_session):
    """`is_registered_business` has three states and NULL is one of them."""
    tenant_id = uuid4()
    profile = await upsert_profile(test_session, tenant_id=tenant_id, tax_id="T1")

    assert profile.is_registered_business is None
    assert profile.is_complete is False


@pytest.mark.asyncio
async def test_unregistered_business_is_complete_without_a_tax_id(test_session):
    """An unregistered merchant answering "no" has answered fully."""
    tenant_id = uuid4()

    profile = await upsert_profile(
        test_session,
        tenant_id=tenant_id,
        is_registered_business=False,
        payout_account_number=ACCOUNT,
    )

    assert profile.is_complete is True
    assert profile.completed_at is not None


@pytest.mark.asyncio
async def test_registered_business_needs_a_tax_id(test_session):
    tenant_id = uuid4()

    profile = await upsert_profile(
        test_session,
        tenant_id=tenant_id,
        is_registered_business=True,
        payout_account_number=ACCOUNT,
    )
    assert profile.is_complete is False

    profile = await upsert_profile(test_session, tenant_id=tenant_id, tax_id="TAX-9")
    assert profile.is_complete is True


@pytest.mark.asyncio
async def test_completed_at_is_stamped_once(test_session):
    """Editing a field later is not completing it again."""
    tenant_id = uuid4()
    profile = await upsert_profile(
        test_session,
        tenant_id=tenant_id,
        is_registered_business=False,
        payout_account_number=ACCOUNT,
    )
    first = profile.completed_at

    profile = await upsert_profile(
        test_session, tenant_id=tenant_id, payout_bank_name="Banque Misr"
    )

    assert profile.completed_at == first
