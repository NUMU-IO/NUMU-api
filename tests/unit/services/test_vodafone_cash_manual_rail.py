"""Vodafone Cash as a manual rail — the behaviours that differ from InstaPay.

The shared machinery (intent lifecycle, proof dedup, auto-approval rules,
expiry sweeper) is already covered by the InstaPay suites and is
literally the same code, so it isn't re-tested here. What IS tested is
every place the two rails diverge, because those are the places a
"just clone InstaPay" implementation would silently get wrong:

  * wallet-number normalization + rejection (the only credential)
  * no QR anywhere on the Vodafone Cash path
  * per-rail reference prefix
  * per-rail amount tolerance (Vodafone charges the *sender* a fee)
  * OCR reading a wallet number instead of an IPA, recipient not sender
  * settings written and read back under the right key
"""

from __future__ import annotations

import pytest

from src.core.entities.instapay import (
    ManualPaymentIntent,
    ManualPaymentMethod,
)
from src.infrastructure.external_services.manual_transfer import (
    MANUAL_TRANSFER_METHODS,
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
    InvalidDestinationError,
    ManualTransferPaymentService,
    default_amount_tolerance_bps,
    default_auto_approve_enabled,
    evaluate,
    generate_reference_code,
    mask_destination,
    normalize_destination,
    normalize_ipa,
    normalize_wallet_number,
    resume_url,
    route_segment,
)
from src.infrastructure.external_services.manual_transfer.merchant_config import (
    ManualConfigError,
    ManualConfigInput,
    build_config_block,
    read_config_view,
)
from src.infrastructure.external_services.vision.proof_vision_service import (
    parse_destination,
    parse_wallet_number,
)

VC = ManualPaymentMethod.VODAFONE_CASH
IP = ManualPaymentMethod.INSTAPAY


# ── Wallet numbers ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "01012345678",
        "1012345678",  # dropped leading zero
        "+201012345678",
        "00201012345678",
        "010 1234 5678",
        "010-1234-5678",
        "٠١٠١٢٣٤٥٦٧٨",
        "۰۱۰۱۲۳۴۵۶۷۸",
    ],
)
def test_wallet_number_normalizes_to_local_form(raw):
    assert normalize_wallet_number(raw) == "01012345678"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0111234567",  # Orange prefix, not Vodafone
        "01112345678",  # Etisalat
        "0101234567",  # one digit short
        "010123456789",  # one digit long
        "merchant@cib",  # an IPA is not a wallet
        "not a number",
    ],
)
def test_bad_wallet_numbers_are_rejected(raw):
    with pytest.raises(InvalidDestinationError):
        normalize_wallet_number(raw)


def test_normalize_destination_dispatches_per_rail():
    assert normalize_destination(VC, "+20 101 234 5678") == "01012345678"
    assert normalize_destination(IP, "  Merchant@CIB ") == "merchant@cib"


def test_ipa_validation_catches_a_typo_that_would_misroute_money():
    with pytest.raises(InvalidDestinationError):
        normalize_ipa("merchantcib")


def test_masking_leaves_enough_to_recognise_your_own_number():
    masked = mask_destination(VC, "01012345678")
    assert masked.startswith("010")
    assert masked.endswith("5678")
    assert "1234" not in masked


# ── Service behaviour ────────────────────────────────────────────────


def _vc_service(**kw) -> ManualTransferPaymentService:
    return ManualTransferPaymentService(destination="01012345678", method=VC, **kw)


def test_both_rails_are_registered_for_checkout_dispatch():
    assert MANUAL_TRANSFER_METHODS == {"instapay", "vodafone_cash"}


def test_vodafone_cash_has_no_qr_and_never_emits_one():
    svc = _vc_service()
    assert svc.supports_qr is False
    qr_payload, _expires = svc.build_intent_payload(
        amount_cents=25_000, reference_code="VF-ABC123"
    )
    assert qr_payload == ""


def test_a_stray_merchant_qr_cannot_leak_onto_a_wallet_checkout():
    # The merchant may have uploaded a QR while configuring InstaPay;
    # passing it here must not surface a scannable code the customer
    # cannot act on (Vodafone Cash starts at *9# or in the app).
    svc = _vc_service(
        qr_image_url="https://cdn.example/qr.png",
        qr_link_url="https://ipn.eg/QR/xyz",
    )
    assert svc.qr_image_url is None
    assert svc.qr_link_url is None


def test_instapay_still_emits_a_scannable_payload():
    svc = ManualTransferPaymentService(destination="merchant@cib", method=IP)
    qr_payload, _ = svc.build_intent_payload(
        amount_cents=25_000, reference_code="NU-ABC123"
    )
    assert qr_payload.startswith("instapay://pay?")
    assert "merchant@cib" in qr_payload


def test_reference_prefixes_are_distinct_per_rail():
    assert IP.reference_prefix == "NU"
    assert VC.reference_prefix == "VF"
    assert generate_reference_code(VC.reference_prefix).startswith("VF-")


def test_checkout_payload_labels_the_destination_as_a_wallet_number():
    from datetime import UTC, datetime, timedelta

    payload = _vc_service(display_name="Vionne").to_checkout_payload(
        reference_code="VF-ABC123",
        qr_payload="",
        amount_cents=25_000,
        currency="egp",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        order_id="00000000-0000-0000-0000-000000000001",
    )
    assert payload["provider"] == "vodafone_cash"
    assert payload["type"] == "manual_verification"
    assert payload["destination"] == "01012345678"
    assert payload["destination_kind"] == "wallet_number"
    assert payload["wallet_number"] == "01012345678"
    # Null, not absent: the storefront branches on these to decide
    # whether to render a QR block at all.
    assert payload["ipa"] is None
    assert payload["supports_qr"] is False
    assert payload["qr_payload"] is None
    assert payload["qr_image_url"] is None
    assert payload["qr_link_url"] is None
    assert payload["currency"] == "EGP"


def test_sender_fee_gets_a_wider_default_amount_tolerance():
    # Vodafone charges the sender, so what lands is short of the order
    # total. A 1% window would soft-block essentially every order.
    assert default_amount_tolerance_bps(IP) == 100
    assert default_amount_tolerance_bps(VC) == 300


# ── OCR ──────────────────────────────────────────────────────────────


def test_ocr_reads_the_recipient_wallet_not_the_sender():
    text = (
        "Transfer successful\n"
        "From 01098765432\n"
        "To 010 1234 5678\n"
        "Amount 250.00 EGP\n"
        "Ref 987654321"
    )
    assert parse_wallet_number(text) == "01012345678"


def test_ocr_reads_an_arabic_receipt():
    text = "تم التحويل\nالمرسل ٠١٠٩٨٧٦٥٤٣٢\nالمستلم ٠١٠١٢٣٤٥٦٧٨\n"
    assert parse_wallet_number(text) == "01012345678"


def test_ocr_returns_none_when_only_the_sender_is_visible():
    # Better to no-op the recipient-match rule than to compare the
    # customer's own number against the merchant's and reject.
    assert parse_wallet_number("From 01098765432\nAmount 250 EGP") is None


def test_parse_destination_dispatches_on_kind():
    slip = "To 01012345678"
    assert parse_destination(slip, "wallet_number") == "01012345678"
    assert parse_destination("To merchant@cib", "ipa") == "merchant@cib"
    # An InstaPay parse of a wallet slip finds nothing — which is
    # exactly the bug the dispatch exists to prevent.
    assert parse_destination(slip, "ipa") is None


# ── Merchant config round-trip ───────────────────────────────────────


@pytest.mark.asyncio
async def test_saving_a_wallet_number_marks_the_rail_configured():
    block, destination = await build_config_block(
        method=VC,
        existing={},
        data=ManualConfigInput(destination="+20 101 234 5678"),
    )
    assert destination == "01012345678"
    assert block["is_configured"] is True
    # First save enables it — this is the gate the payment-settings
    # toggle checks, and the reason Vodafone Cash was previously
    # impossible to turn on.
    assert block["enabled"] is True
    assert block["encrypted_credentials"]
    # No QR keys on a rail without a QR.
    assert "qr_image_url" not in block
    assert "qr_link_url" not in block
    assert block["ocr_amount_tolerance_bps"] == 300

    view = await read_config_view(method=VC, block=block)
    assert view["is_configured"] is True
    assert view["destination_masked"] == "010****5678"


@pytest.mark.asyncio
async def test_a_partial_update_keeps_the_stored_wallet_number():
    block, _ = await build_config_block(
        method=VC,
        existing={},
        data=ManualConfigInput(destination="01012345678", fallback_phone="0221234"),
    )
    # The dashboard only ever shows the number masked, so an edit that
    # changes a threshold must not wipe it.
    updated, destination = await build_config_block(
        method=VC,
        existing=block,
        data=ManualConfigInput(
            destination=None,
            display_name="Vionne",
            auto_approve_threshold_cents=99_000,
        ),
    )
    assert destination == "01012345678"
    assert updated["auto_approve_threshold_cents"] == 99_000
    view = await read_config_view(method=VC, block=updated)
    assert view["destination_masked"] == "010****5678"
    assert view["fallback_phone"] == "0221234"


@pytest.mark.asyncio
async def test_first_save_without_a_wallet_number_is_rejected():
    with pytest.raises(ManualConfigError) as exc:
        await build_config_block(
            method=VC, existing={}, data=ManualConfigInput(display_name="Vionne")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_a_malformed_wallet_number_is_rejected_on_save():
    with pytest.raises(ManualConfigError) as exc:
        await build_config_block(
            method=VC, existing={}, data=ManualConfigInput(destination="0111234567")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_first_save_enables_even_over_the_default_settings_block():
    """Every store carries a disabled placeholder block before setup.

    Treating that placeholder as "an existing config" left a
    freshly-configured rail switched off, so the merchant saved their
    wallet number and nothing appeared at checkout.
    """
    placeholder = {"enabled": False, "is_configured": False, "last_configured": None}
    block, _ = await build_config_block(
        method=VC,
        existing=placeholder,
        data=ManualConfigInput(destination="01012345678"),
    )
    assert block["enabled"] is True


@pytest.mark.asyncio
async def test_editing_thresholds_does_not_re_enable_a_disabled_rail():
    block, _ = await build_config_block(
        method=VC, existing={}, data=ManualConfigInput(destination="01012345678")
    )
    block["enabled"] = False
    updated, _ = await build_config_block(
        method=VC,
        existing=block,
        data=ManualConfigInput(auto_approve_daily_count=3),
    )
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_instapay_config_still_round_trips_through_the_shared_path():
    block, destination = await build_config_block(
        method=IP,
        existing={},
        data=ManualConfigInput(
            destination="Merchant@CIB",
            qr_link_url="https://ipn.eg/QR/xyz",
        ),
    )
    assert destination == "merchant@cib"
    assert block["qr_link_url"] == "https://ipn.eg/QR/xyz"
    view = await read_config_view(method=IP, block=block)
    assert view["destination_masked"].endswith("@cib")
    assert view["ocr_amount_tolerance_bps"] == 100


# ── Entity ───────────────────────────────────────────────────────────


def test_intent_defaults_to_instapay_so_existing_rows_are_unchanged():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    intent = ManualPaymentIntent.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        reference_code="NU-ABC123",
        display_destination="merchant@cib",
        amount_cents=25_000,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        qr_payload="instapay://pay?ipa=merchant@cib",
    )
    assert intent.method is IP
    # Back-compat alias for the InstaPay-era call sites.
    assert intent.display_ipa == "merchant@cib"


def test_a_vodafone_cash_intent_carries_its_rail():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    intent = ManualPaymentIntent.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        reference_code="VF-ABC123",
        display_destination="01012345678",
        amount_cents=25_000,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        qr_payload="",
        method=VC,
    )
    assert intent.method is VC
    assert intent.display_destination == "01012345678"
    assert intent.qr_payload == ""


# ── Resume links ─────────────────────────────────────────────────────


def test_route_segments_are_url_shaped_not_provider_codes():
    # These land in emails and address bars; "vodafone_cash" would too.
    assert route_segment(IP) == "instapay"
    assert route_segment(VC) == "vodafone-cash"


def test_resume_link_carries_the_reference_that_authorizes_it():
    # The proof endpoints accept a session cookie OR the reference code.
    # Someone following this link out of their email has neither a
    # session nor a way to type the code, so without ?ref= the link
    # 403s for exactly the people it exists for.
    url = resume_url(
        VC,
        base_url="https://vionne.numueg.app",
        order_id="abc-123",
        reference_code="VF-EM7M5Q",
    )
    assert url == "https://vionne.numueg.app/vodafone-cash/abc-123?ref=VF-EM7M5Q"


def test_resume_link_omits_the_query_when_there_is_no_reference():
    # Older events carry no reference code. The page then asks for it
    # rather than sending "?ref=None" upstream.
    url = resume_url(IP, base_url="https://vionne.numueg.app/", order_id="abc-123")
    assert url == "https://vionne.numueg.app/instapay/abc-123"


# ── Auto-approval is opt-in on a wallet rail ─────────────────────────


def _intent(method=VC):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    return ManualPaymentIntent.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        reference_code="VF-ABC123",
        display_destination="01012345678",
        amount_cents=15_001,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        qr_payload="",
        method=method,
    )


def _proof():
    from uuid import uuid4

    from src.core.entities.instapay import PaymentProof

    return PaymentProof.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        proof_image_key="k",
        proof_image_hash=b"h",
        transaction_ref="123456789",
    )


def _config(**kw) -> AutoApprovalConfig:
    base = {
        "threshold_cents": 50_000,
        "daily_cap_cents": 500_000,
        "daily_count_cap": 10,
    }
    base.update(kw)
    return AutoApprovalConfig(**base)


def _facts(total=15_001) -> AutoApprovalFacts:
    return AutoApprovalFacts(
        order_total_cents=total,
        daily_auto_approved_count=0,
        daily_auto_approved_cents=0,
    )


def test_wallet_rail_does_not_auto_approve_before_the_merchant_opts_in():
    """The exact case a merchant hit: any photo, order under the threshold.

    With no OCR provider every image rule no-ops, so the only gates left
    were an amount threshold and daily caps — neither of which looks at
    the receipt. A 150 EGP order sailed through on an arbitrary picture.
    """
    decision: AutoApprovalDecision = evaluate(
        intent=_intent(),
        proof=_proof(),
        config=_config(enabled=False),
        facts=_facts(),
    )
    assert decision.approved is False
    assert "auto_approval_disabled" in decision.reasons
    # Soft: it goes to the merchant, it is not thrown back at the buyer.
    assert decision.soft_block is True


def test_the_switch_beats_every_threshold():
    # Well under the threshold, caps untouched — still not approved.
    decision = evaluate(
        intent=_intent(),
        proof=_proof(),
        config=_config(enabled=False, threshold_cents=10_000_000),
        facts=_facts(total=1),
    )
    assert decision.approved is False


def test_turning_it_on_restores_auto_approval():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(),
        config=_config(enabled=True),
        facts=_facts(),
    )
    assert decision.approved is True
    assert decision.reasons == []


def test_instapay_is_unchanged():
    # Months of live behaviour: on by default, and still approves.
    assert default_auto_approve_enabled(IP) is True
    decision = evaluate(
        intent=_intent(IP), proof=_proof(), config=_config(), facts=_facts()
    )
    assert decision.approved is True


def test_vodafone_cash_defaults_to_off():
    assert default_auto_approve_enabled(VC) is False


@pytest.mark.asyncio
async def test_a_new_wallet_config_stores_auto_approval_off():
    block, _ = await build_config_block(
        method=VC, existing={}, data=ManualConfigInput(destination="01012345678")
    )
    assert block["auto_approve_enabled"] is False
    view = await read_config_view(method=VC, block=block)
    assert view["auto_approve_enabled"] is False


@pytest.mark.asyncio
async def test_a_store_configured_before_the_switch_existed_reads_as_off():
    """The merchant who reported this already has a saved block.

    Their stored config predates the flag, so the fallback is what
    protects them — a code default alone would not have.
    """
    legacy = {
        "enabled": True,
        "is_configured": True,
        "encrypted_credentials": None,
        "auto_approve_threshold_cents": 50_000,
    }
    view = await read_config_view(
        method=VC, block={**legacy, "encrypted_credentials": None}
    )
    # No credentials -> not configured; the interesting case is the block
    # read through build_config_block, below.
    assert view["is_configured"] is False

    block, _ = await build_config_block(
        method=VC,
        existing={"auto_approve_threshold_cents": 50_000},
        data=ManualConfigInput(destination="01012345678"),
    )
    assert block["auto_approve_enabled"] is False


@pytest.mark.asyncio
async def test_a_merchant_can_turn_it_on_and_it_sticks():
    block, _ = await build_config_block(
        method=VC,
        existing={},
        data=ManualConfigInput(destination="01012345678", auto_approve_enabled=True),
    )
    assert block["auto_approve_enabled"] is True
    # A later partial save that omits the flag must not switch it back off.
    updated, _ = await build_config_block(
        method=VC, existing=block, data=ManualConfigInput(auto_approve_daily_count=3)
    )
    assert updated["auto_approve_enabled"] is True


@pytest.mark.asyncio
async def test_instapay_config_keeps_auto_approval_on():
    block, _ = await build_config_block(
        method=IP, existing={}, data=ManualConfigInput(destination="merchant@cib")
    )
    assert block["auto_approve_enabled"] is True


# ── The first-time-setup round trip ──────────────────────────────────
#
# The regression a merchant actually hit: auto-approval defaulted OFF, yet a
# fake picture still marked their order PAID. The default was fine; the
# ROUND TRIP switched it on.
#
#   open the card (nothing configured)  -> API says auto_approve_enabled
#   -> hub hydrates whatever it was told
#   -> merchant saves their wallet number, echoing it back
#   -> stored
#
# Any layer that answers "on" when it means "I don't know" turns setup into
# an opt-in the merchant never made.


@pytest.mark.asyncio
async def test_the_not_configured_view_reports_auto_approval_off():
    """This is the value the hub hydrates from before anything is saved."""
    view = await read_config_view(method=VC, block={})
    assert view["auto_approve_enabled"] is False


@pytest.mark.asyncio
async def test_an_unreadable_block_still_reports_off():
    view = await read_config_view(
        method=VC,
        block={"encrypted_credentials": "not-base64", "encryption_key_id": "gone"},
    )
    assert view.get("unreadable") is True
    assert view["auto_approve_enabled"] is False


@pytest.mark.asyncio
async def test_setting_up_the_rail_does_not_switch_auto_approval_on():
    """Replays the whole loop, echoing the API's answer back like the hub does."""
    # 1. Merchant opens the card. Nothing configured yet.
    view = await read_config_view(method=VC, block={})
    hydrated = view["auto_approve_enabled"]

    # 2. They type a wallet number and save. The hub sends back what it was
    #    given — which is exactly how the bug propagated.
    block, _ = await build_config_block(
        method=VC,
        existing={},
        data=ManualConfigInput(
            destination="01012345678", auto_approve_enabled=hydrated
        ),
    )

    # 3. Nothing they did asked for auto-approval.
    assert block["auto_approve_enabled"] is False

    # 4. And the proof engine agrees, which is the part that costs money.
    decision = evaluate(
        intent=_intent(),
        proof=_proof(),
        config=_config(enabled=block["auto_approve_enabled"]),
        facts=_facts(),
    )
    assert decision.approved is False
    assert "auto_approval_disabled" in decision.reasons


@pytest.mark.asyncio
async def test_the_same_loop_keeps_instapay_on():
    view = await read_config_view(method=IP, block={})
    block, _ = await build_config_block(
        method=IP,
        existing={},
        data=ManualConfigInput(
            destination="merchant@cib",
            auto_approve_enabled=view["auto_approve_enabled"],
        ),
    )
    assert block["auto_approve_enabled"] is True
