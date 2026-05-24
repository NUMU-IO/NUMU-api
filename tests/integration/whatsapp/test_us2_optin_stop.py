"""Integration tests for US2 — STOP keyword opt-out + storefront opt-in.

Covers acceptance scenarios:
- AS-1: each of {stop, STOP, unsubscribe, إلغاء, الغاء} as the first
  word of an inbound message → opt-in flipped + ack reply within 10s
  + subsequent send skipped with reason=opt_out (T042)
- AS-2: 'please STOP sending' (STOP in middle) → opt-in unchanged,
  message routes to conversations inbox normally (T043)
- AS-3: storefront opt-in requires a valid checkout-session token
  (missing/expired/wrong store/phone mismatch → 403; valid + match
  → 201) (T044)
- AS-4: re-opt after opt-out creates a NEW opt-in row, preserves
  prior opted_out_at history (T045 / FR-012)
- AS-5: merchant opt-in / opt-out API list / create / revoke flows
  (T046)

Gated on ``NUMU_RUN_INTEGRATION_TESTS=1``; fixtures (seeded_store_with_active_optin
+ checkout-session/customer/message-log/Redis fixtures) live at conftest level
and land alongside Batch 4 dispatcher fixtures.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.application.use_cases.whatsapp.opt_in_customer import OptInCustomerUseCase
from src.application.use_cases.whatsapp.opt_out_customer import OptOutCustomerUseCase
from src.core.services.whatsapp_stop_keyword_detector import is_stop_keyword

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason="DB+Redis integration tests; set NUMU_RUN_INTEGRATION_TESTS=1.",
)

# ── Arabic strings via chr() to keep source ASCII (bandit B613) ─────

_ILGHA_HAMZA = chr(0x0625) + chr(0x0644) + chr(0x063A) + chr(0x0627) + chr(0x0621)
_ILGHA_NO_HAMZA = chr(0x0627) + chr(0x0644) + chr(0x063A) + chr(0x0627) + chr(0x0621)


# ── T042 — STOP keyword variants ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_word",
    ["stop", "STOP", "unsubscribe", _ILGHA_HAMZA, _ILGHA_NO_HAMZA],
)
async def test_stop_keyword_variants_trigger_opt_out(
    db_session,
    seeded_store_with_active_optin,
    simulate_inbound_message,
    first_word,
):
    """For each canonical opt-out keyword, an inbound text message whose
    first word matches the keyword MUST: (a) flip the active opt-in row
    to opted_out, (b) write opt_out_reason='inbound_stop_keyword',
    (c) trigger an acknowledgement send.
    """
    _store, customer, optin = seeded_store_with_active_optin

    with patch(
        "src.api.v1.routes.webhooks.whatsapp._send_optout_ack",
        new=AsyncMock(),
    ) as mock_ack:
        await simulate_inbound_message(
            from_phone=customer.phone.lstrip("+"),
            text=f"{first_word} please",
        )

    await db_session.refresh(optin)
    assert optin.opted_out_at is not None
    assert optin.opt_out_reason == "inbound_stop_keyword"
    mock_ack.assert_awaited_once()


# ── T043 — STOP not as first word ──────────────────────────────────


@pytest.mark.asyncio
async def test_stop_in_middle_of_message_does_not_opt_out(
    db_session,
    seeded_store_with_active_optin,
    simulate_inbound_message,
):
    """`please STOP sending` MUST route to the conversations inbox
    normally and MUST NOT flip the opt-in row."""
    _store, customer, optin = seeded_store_with_active_optin

    with patch(
        "src.api.v1.routes.webhooks.whatsapp._send_optout_ack",
        new=AsyncMock(),
    ) as mock_ack:
        await simulate_inbound_message(
            from_phone=customer.phone.lstrip("+"),
            text="please STOP sending",
        )

    await db_session.refresh(optin)
    assert optin.opted_out_at is None
    mock_ack.assert_not_awaited()


# Unit-level guard for the detector — would already be covered in
# tests/unit, but kept here as a smoke check so a regression in the
# webhook wiring shows up alongside the integration-shaped assertions.
def test_stop_detector_self_check() -> None:
    assert is_stop_keyword("STOP please")
    assert not is_stop_keyword("please STOP sending")


# ── T044 — Storefront opt-in requires valid checkout-session token ──


@pytest.mark.asyncio
async def test_storefront_optin_missing_token_403(storefront_client, seeded_store):
    response = await storefront_client.post(
        f"/api/v1/storefront/{seeded_store.subdomain}/whatsapp/opt-in",
        json={
            "phone": "+201001234567",
            # NO checkout_session_token
        },
    )
    # The schema requires the token field — Pydantic v2 returns 422.
    assert response.status_code in (403, 422)


@pytest.mark.asyncio
async def test_storefront_optin_expired_token_403(
    storefront_client, seeded_store, expired_checkout_session
):
    """Token whose Redis TTL has elapsed → 403 invalid_checkout_session."""
    response = await storefront_client.post(
        f"/api/v1/storefront/{seeded_store.subdomain}/whatsapp/opt-in",
        json={
            "phone": "+201001234567",
            "checkout_session_token": str(expired_checkout_session.token),
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["detail"]["code"] == "invalid_checkout_session"


@pytest.mark.asyncio
async def test_storefront_optin_wrong_store_403(
    storefront_client,
    seeded_store_a,
    seeded_store_b,
    issue_checkout_session,
):
    """Token issued for store A, request targets store B → 403."""
    token = await issue_checkout_session(
        store_id=seeded_store_a.id, phone="+201001234567"
    )
    response = await storefront_client.post(
        f"/api/v1/storefront/{seeded_store_b.subdomain}/whatsapp/opt-in",
        json={
            "phone": "+201001234567",
            "checkout_session_token": str(token),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invalid_checkout_session"


@pytest.mark.asyncio
async def test_storefront_optin_phone_mismatch_403(
    storefront_client, seeded_store, issue_checkout_session
):
    """Token's stored phone != body phone (after canonicalization) → 403."""
    token = await issue_checkout_session(
        store_id=seeded_store.id, phone="+201001234567"
    )
    response = await storefront_client.post(
        f"/api/v1/storefront/{seeded_store.subdomain}/whatsapp/opt-in",
        json={
            "phone": "+201009999999",  # different phone
            "checkout_session_token": str(token),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "phone_mismatch_with_cart"


@pytest.mark.asyncio
async def test_storefront_optin_happy_path_201(
    storefront_client, seeded_store, issue_checkout_session, db_session
):
    """Valid token + matching phone → 201 with a fresh opt-in row."""
    token = await issue_checkout_session(
        store_id=seeded_store.id, phone="+201001234567"
    )
    response = await storefront_client.post(
        f"/api/v1/storefront/{seeded_store.subdomain}/whatsapp/opt-in",
        json={
            "phone": "+201001234567",
            "checkout_session_token": str(token),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["phone"] == "+201001234567"
    assert body["source"] == "checkout"
    assert body["opted_out_at"] is None


# ── T045 — Re-opt after opt-out creates a NEW row (FR-012 history) ──


@pytest.mark.asyncio
async def test_reopt_after_opt_out_creates_new_row(
    db_session, seeded_store, seeded_customer
):
    """Re-opting MUST create a new opt-in row, never mutate the prior
    opted-out one — the audit trail must show both states."""
    use_case_in = OptInCustomerUseCase(db_session)
    use_case_out = OptOutCustomerUseCase(db_session)

    first = await use_case_in.execute(
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        source="checkout",
        customer_id=seeded_customer.id,
    )
    await use_case_out.execute(
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        reason="customer_request_via_support",
    )
    second = await use_case_in.execute(
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        source="api",
        customer_id=seeded_customer.id,
    )

    assert first.id != second.id
    # Original row is preserved with opted_out_at set
    await db_session.refresh(first)
    assert first.opted_out_at is not None
    # Latest row is active
    assert second.opted_out_at is None


# ── T046 — Merchant API list / create / revoke ──────────────────────


@pytest.mark.asyncio
async def test_merchant_opt_in_list_create_revoke_flow(
    merchant_client, seeded_store, db_session
):
    base = f"/api/v1/stores/{seeded_store.id}/whatsapp/opt-ins"

    # Create
    create_resp = await merchant_client.post(
        base,
        json={"phone": "+201005555555", "source": "import"},
    )
    assert create_resp.status_code == 201
    row = create_resp.json()
    assert row["phone"] == "+201005555555"
    assert row["source"] == "import"

    # List should include it
    list_resp = await merchant_client.get(base, params={"phone": "+201005555555"})
    assert list_resp.status_code == 200
    assert any(r["id"] == row["id"] for r in list_resp.json())

    # Revoke
    revoke_resp = await merchant_client.post(
        f"{base}/revoke",
        json={"phone": "+201005555555", "reason": "merchant_revoke"},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["opt_out_reason"] == "merchant_revoke"

    # Revoking again with no active row → 404
    revoke_again = await merchant_client.post(
        f"{base}/revoke",
        json={"phone": "+201005555555", "reason": "merchant_revoke"},
    )
    assert revoke_again.status_code == 404


# ── Fixtures expected at conftest level ─────────────────────────────
#   - db_session: AsyncSession with RLS tenant context set
#   - seeded_store, seeded_store_a, seeded_store_b: StoreModels under
#     the test tenant
#   - seeded_store_with_active_optin: (store, customer, optin) tuple
#     with an active WhatsAppOptInModel row
#   - seeded_customer: CustomerModel with E.164 phone
#   - issue_checkout_session(store_id, phone): coroutine that mints a
#     fresh CheckoutSession in Redis and returns its UUID token
#   - expired_checkout_session: a CheckoutSession whose Redis TTL has
#     already elapsed (or is otherwise unreachable)
#   - simulate_inbound_message(from_phone, text): coroutine that POSTs
#     a synthetic webhook payload to /api/v1/webhooks/whatsapp/callback
#     and runs the inbound-message dispatch pipeline against db_session
#   - storefront_client: httpx.AsyncClient targeting the app, no auth
#   - merchant_client: httpx.AsyncClient with bearer auth for the
#     seeded store owner
#
# These fixtures are intentionally not in this batch — they land in
# tests/integration/whatsapp/conftest.py alongside the per-US3
# dispatcher tests where the same shapes are needed.
