"""Unit tests for the OCR text parsers + auto-approval rule glue.

The vision providers are integration-tested separately (Google needs
an API key, the HF Spaces need network). Here we lock down the
deterministic surface:

  * ``parse_amount_egp`` handles ASCII + Arabic-Indic digits, common
    bank-app receipt formats, and rejects nonsense.
  * ``parse_ipa`` only returns IPAs with a registered bank suffix.
  * The new auto-approval rules fire when (and only when) the
    merchant has opted in *and* OCR returned an ``ok`` result with
    a non-null extracted value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.core.entities.instapay import (
    InstapayIntent,
    InstapayIntentStatus,
    PaymentProof,
)
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalFacts,
    evaluate,
)
from src.infrastructure.external_services.vision.proof_vision_service import (
    parse_amount_egp,
    parse_ipa,
    parse_note,
    parse_recipient_name,
    parse_transaction_ref,
)

# ── parse_amount_egp ─────────────────────────────────────────────────


def test_parse_amount_ascii_egp():
    assert parse_amount_egp("Total: 500.00 EGP") == 50_000


def test_parse_amount_currency_leading():
    """InstaPay's own UI puts the token before the digits: ``EGP 62.00``."""
    assert parse_amount_egp("Send\nEGP 62.00\nto") == 6_200


def test_parse_amount_currency_leading_arabic():
    assert parse_amount_egp("ج.م ٥٠٠٫٠٠") == 50_000


def test_parse_amount_arabic_indic_digits():
    # Arabic-Indic digits + Arabic currency suffix — common in
    # CIB / NBE bank apps even when UI is English.
    assert parse_amount_egp("الإجمالي: ٥٠٠٫٠٠ ج.م") == 50_000


def test_parse_amount_picks_largest_match():
    """Receipts often show several numbers — pick the headline one."""
    text = "Fee 5 EGP\nTotal: 500.00 EGP\nReceived 5.00 EGP"
    assert parse_amount_egp(text) == 50_000


def test_parse_amount_with_thousands_separator():
    assert parse_amount_egp("Total: 1,250.50 EGP") == 125_050


def test_parse_amount_returns_none_when_no_amount():
    assert parse_amount_egp("بدون مبلغ") is None
    assert parse_amount_egp("") is None
    assert parse_amount_egp(None) is None  # type: ignore[arg-type]


def test_parse_amount_caps_implausible_values():
    """A 9-digit transaction reference must not be misread as cents."""
    assert parse_amount_egp("Ref 999999999 EGP") is None


# ── parse_ipa ────────────────────────────────────────────────────────


def test_parse_ipa_recognises_known_suffix():
    assert parse_ipa("To: merchant@cib") == "merchant@cib"


def test_parse_ipa_lowercases():
    assert parse_ipa("Recipient: Merchant@CIB") == "merchant@cib"


def test_parse_ipa_rejects_email_addresses():
    """Customer email mustn't get confused for a recipient IPA."""
    assert parse_ipa("From customer@gmail.com to anywhere") is None


def test_parse_ipa_picks_first_known():
    text = "From customer@gmail.com → To merchant@cib → ack@unknown"
    assert parse_ipa(text) == "merchant@cib"


def test_parse_ipa_returns_none_when_empty():
    assert parse_ipa("") is None


def test_parse_ipa_skips_sender_side():
    """Bank receipts label sender vs recipient. Sender IPA is not us."""
    text = (
        "Approved Transaction\n"
        "From\n"
        "yahiasheriif2@instapay\n"
        "ARAB BANK\n"
        "To\n"
        "merchant@cib\n"
    )
    assert parse_ipa(text) == "merchant@cib"


def test_parse_ipa_returns_none_when_only_sender_visible():
    """Arab Bank masks the recipient — only sender is visible."""
    text = "From\nyahiasheriif2@instapay\nTo Instapay\nNagwa F**** H****\n"
    # No recipient-side IPA visible → return None so the
    # ``ocr_ipa_mismatch`` rule no-ops cleanly instead of
    # false-positive matching the sender against the merchant.
    assert parse_ipa(text) is None


def test_parse_ipa_no_anchors_falls_back_to_first():
    """OCR text without From/To labels → take the first registered IPA."""
    text = "Receipt for merchant@cib payment confirmed."
    assert parse_ipa(text) == "merchant@cib"


# ── parse_note / parse_transaction_ref / parse_recipient_name ────────


_ARAB_BANK_RECEIPT = (
    "Your transaction was successful\n"
    "1 EGP\n"
    "Transfer Amount\n"
    "ARAB BANK\n"
    "From\n"
    "yahiasheriif2@instapay\n"
    "ACCOUNT **489409\n"
    "To\n"
    "غادة ر****ح***ا***\n"
    "ghadaramadanhassan@instapay\n"
    "Reference 714363474758\n"
    "Date 27 Apr 2026 08:29 PM\n"
    "Note Living Expenses NU-KTJN9V\n"
    "POWERED BY\n"
)


def test_parse_note_extracts_bank_app_note():
    note = parse_note(_ARAB_BANK_RECEIPT)
    assert note is not None
    assert "Living Expenses" in note
    assert "NU-KTJN9V" in note


def test_parse_note_returns_none_when_absent():
    text = "Receipt with no labelled note here\nRef 12345\n"
    assert parse_note(text) is None


def test_parse_transaction_ref_anchored_to_label():
    """Bank ref must come from the labelled section, not a stray phone."""
    text = "Phone: 01010396523\nReference 714363474758\nDate 27 Apr 2026\n"
    assert parse_transaction_ref(text) == "714363474758"


def test_parse_transaction_ref_returns_none_when_unlabelled():
    text = "Just some digits 1234567890 with no label\n"
    assert parse_transaction_ref(text) is None


def test_parse_recipient_name_captures_to_block():
    name = parse_recipient_name(_ARAB_BANK_RECEIPT)
    assert name is not None
    # Should include the masked Arabic name and stop before the next
    # major section anchor.
    assert "غادة" in name
    assert "Reference" not in name


def test_parse_recipient_name_returns_none_without_to_anchor():
    text = "From\nfoo@instapay\nReference 1234\n"
    assert parse_recipient_name(text) is None


# ── New auto-approval rules ─────────────────────────────────────────


def _proof_with_extras(
    *,
    ocr_status: str | None = "ok",
    ocr_amount: int | None = None,
    ocr_ipa: str | None = None,
    ocr_note: str | None = None,
    ocr_txn_ref: str | None = None,
    ocr_recipient: str | None = None,
):
    return PaymentProof.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        proof_image_key="test/key.bin",
        proof_image_hash=b"\x00" * 32,
        transaction_ref="REF123",
        ocr_status=ocr_status,
        ocr_extracted_amount_cents=ocr_amount,
        ocr_extracted_ipa=ocr_ipa,
        ocr_extracted_note=ocr_note,
        ocr_extracted_transaction_ref=ocr_txn_ref,
        ocr_extracted_recipient_name=ocr_recipient,
    )


def test_note_missing_reference_fires_when_opted_in():
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(ocr_note="Living Expenses"),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_note_contains_reference=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            intent_reference_code="NU-KTJN9V",
        ),
    )
    assert "ocr_note_missing_reference" in decision.reasons


def test_note_with_reference_passes():
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(ocr_note="Order NU-KTJN9V thanks"),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_note_contains_reference=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            intent_reference_code="nu-ktjn9v",  # case-insensitive
        ),
    )
    assert "ocr_note_missing_reference" not in decision.reasons


def test_transaction_ref_mismatch_fires():
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(ocr_txn_ref="714363474758"),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_transaction_ref_match=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            submitted_transaction_ref="999999999999",
        ),
    )
    assert "ocr_transaction_ref_mismatch" in decision.reasons


def test_transaction_ref_match_passes_with_punctuation():
    """Bank app may show ref with spaces; form input may not. Strip
    non-digits before comparing."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(ocr_txn_ref="714 363 474 758"),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_transaction_ref_match=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            submitted_transaction_ref="714363474758",
        ),
    )
    assert "ocr_transaction_ref_mismatch" not in decision.reasons


def test_recipient_name_token_match_passes():
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(
            ocr_recipient="غادة ر**** ح***ا***",
        ),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_recipient_name_match=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            merchant_recipient_name_token="غادة",
        ),
    )
    assert "ocr_recipient_name_mismatch" not in decision.reasons


def test_recipient_name_token_mismatch_fires():
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(ocr_recipient="Mohamed F**** A****"),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_recipient_name_match=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            merchant_recipient_name_token="Nagwa",
        ),
    )
    assert "ocr_recipient_name_mismatch" in decision.reasons


def test_new_rules_skip_when_ocr_failed():
    """failed_gpu / failed_timeout etc must never fire any of the new
    rules — provider degradation can't escalate into customer behaviour."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof_with_extras(
            ocr_status="failed_gpu",
            ocr_note="garbage",
            ocr_txn_ref="000",
            ocr_recipient="anyone",
        ),
        config=AutoApprovalConfig(
            threshold_cents=100_000,
            daily_cap_cents=10_000_000,
            daily_count_cap=100,
            require_note_contains_reference=True,
            require_transaction_ref_match=True,
            require_recipient_name_match=True,
        ),
        facts=AutoApprovalFacts(
            order_total_cents=50_000,
            daily_auto_approved_count=0,
            daily_auto_approved_cents=0,
            intent_reference_code="NU-WHATEVER",
            submitted_transaction_ref="9999",
            merchant_recipient_name_token="Nagwa",
        ),
    )
    for r in (
        "ocr_note_missing_reference",
        "ocr_transaction_ref_mismatch",
        "ocr_recipient_name_mismatch",
    ):
        assert r not in decision.reasons


def test_parse_ipa_qr_paid_arab_bank_receipt():
    """QR-initiated InstaPay transfers expose the recipient IPA in the
    receipt body. Context-aware parser must skip the sender (From)
    and return the recipient (To)."""
    text = (
        "Your transaction was successful\n"
        "1 EGP\n"
        "Transfer Amount\n"
        "ARAB BANK\n"
        "From\n"
        "yahiasheriif2@instapay\n"
        "ACCOUNT **489409\n"
        "To\n"
        "غادة ر****ح***ا***\n"
        "ghadaramadanhassan@instapay\n"
        "Reference 714363474758\n"
    )
    assert parse_ipa(text) == "ghadaramadanhassan@instapay"


# ── Auto-approval rules ──────────────────────────────────────────────


def _intent(*, expired: bool = False) -> InstapayIntent:
    now = datetime.now(UTC)
    return InstapayIntent(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        reference_code="NU-TEST",
        display_ipa="merchant@cib",
        display_phone=None,
        amount_cents=50_000,
        status=InstapayIntentStatus.AWAITING_PAYMENT,
        expires_at=now - timedelta(minutes=1)
        if expired
        else now + timedelta(minutes=10),
        qr_payload="instapay://...",
    )


def _proof(
    *,
    ocr_status: str | None = None,
    ocr_amount: int | None = None,
    ocr_ipa: str | None = None,
) -> PaymentProof:
    return PaymentProof.new(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        proof_image_key="test/key.bin",
        proof_image_hash=b"\x00" * 32,
        transaction_ref="REF123",
        ocr_status=ocr_status,
        ocr_extracted_amount_cents=ocr_amount,
        ocr_extracted_ipa=ocr_ipa,
    )


def _config(
    *,
    require_amount: bool = False,
    require_ipa: bool = False,
    tolerance_bps: int = 100,
) -> AutoApprovalConfig:
    return AutoApprovalConfig(
        threshold_cents=100_000,
        daily_cap_cents=10_000_000,
        daily_count_cap=100,
        require_ocr_amount_match=require_amount,
        require_ocr_ipa_match=require_ipa,
        ocr_amount_tolerance_bps=tolerance_bps,
    )


def _facts(*, merchant_ipa: str | None = "merchant@cib") -> AutoApprovalFacts:
    return AutoApprovalFacts(
        order_total_cents=50_000,
        daily_auto_approved_count=0,
        daily_auto_approved_cents=0,
        merchant_ipa=merchant_ipa,
    )


def test_ocr_amount_mismatch_fires_when_opted_in():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_amount=10_000),  # 100 vs 500 EGP
        config=_config(require_amount=True),
        facts=_facts(),
    )
    assert "ocr_amount_mismatch" in decision.reasons
    assert decision.approved is False


def test_ocr_amount_match_within_tolerance_passes():
    """1 EGP off on a 500 EGP order is ~20 bps — under the 100 bps default."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_amount=49_900),  # 1 EGP under
        config=_config(require_amount=True),
        facts=_facts(),
    )
    assert "ocr_amount_mismatch" not in decision.reasons


def test_ocr_amount_rule_skipped_when_flag_off():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_amount=10_000),  # would mismatch
        config=_config(require_amount=False),
        facts=_facts(),
    )
    assert "ocr_amount_mismatch" not in decision.reasons


def test_ocr_amount_rule_skipped_when_status_failed():
    """Provider failure must never escalate into customer-visible behaviour."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="failed", ocr_amount=10_000),
        config=_config(require_amount=True),
        facts=_facts(),
    )
    assert "ocr_amount_mismatch" not in decision.reasons


def test_ocr_ipa_mismatch_fires_when_opted_in():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_ipa="impostor@cib"),
        config=_config(require_ipa=True),
        facts=_facts(merchant_ipa="merchant@cib"),
    )
    assert "ocr_ipa_mismatch" in decision.reasons


def test_ocr_ipa_match_passes():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_ipa="merchant@cib"),
        config=_config(require_ipa=True),
        facts=_facts(merchant_ipa="merchant@cib"),
    )
    assert "ocr_ipa_mismatch" not in decision.reasons


def test_ocr_ipa_match_case_insensitive():
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_ipa="Merchant@CIB"),
        config=_config(require_ipa=True),
        facts=_facts(merchant_ipa="merchant@cib"),
    )
    assert "ocr_ipa_mismatch" not in decision.reasons


def test_ocr_ipa_rule_skipped_when_merchant_ipa_unknown():
    """Without merchant IPA we have nothing to compare against → no-op."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status="ok", ocr_ipa="anyone@cib"),
        config=_config(require_ipa=True),
        facts=_facts(merchant_ipa=None),
    )
    assert "ocr_ipa_mismatch" not in decision.reasons


def test_no_ocr_rules_fire_with_pre_phase_c_proof():
    """Predates Phase C: ocr_status is None — both rules silent."""
    decision = evaluate(
        intent=_intent(),
        proof=_proof(ocr_status=None),
        config=_config(require_amount=True, require_ipa=True),
        facts=_facts(),
    )
    assert "ocr_amount_mismatch" not in decision.reasons
    assert "ocr_ipa_mismatch" not in decision.reasons
    assert decision.approved is True
