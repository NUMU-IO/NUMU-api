"""Truth-table coverage for ``WhatsAppSendGuard.check`` (FR-036–FR-038, FR-029).

Pure-logic tests — no DB, no I/O. Every guard branch from FR-037 has at
least one row.
"""

import pytest

from src.core.enums.whatsapp import SendSkipReason, TemplateCategory
from src.core.services.whatsapp_send_guard import (
    OPT_IN_BYPASS_ALLOWLIST,
    GuardContext,
    check,
)


def _ctx(**overrides) -> GuardContext:
    """A 'happy-path utility template send' context. Override fields to
    exercise specific guard branches."""
    defaults: dict = {
        "phone": "+201001234567",
        "template_name": "order_confirmation",
        "template_category": TemplateCategory.UTILITY,
        "template_status": "APPROVED",
        "store_has_credentials": True,
        "store_credentials_marked_invalid": False,
        "notification_setting_enabled": True,
        "has_active_opt_in": False,  # utility allowed without opt-in
        "has_opt_out": False,
        "window_is_open": True,
        "already_sent": False,
    }
    defaults.update(overrides)
    return GuardContext(**defaults)


# ── Happy paths ─────────────────────────────────────────────────────


def test_utility_template_allowed_without_opt_in() -> None:
    """FR-011 — utility templates bypass active-opt-in requirement."""
    decision = check(_ctx(template_category=TemplateCategory.UTILITY))
    assert decision.allowed
    assert decision.reason is None


def test_authentication_template_allowed_without_opt_in() -> None:
    """FR-011 — authentication category treated like utility."""
    decision = check(_ctx(template_category=TemplateCategory.AUTHENTICATION))
    assert decision.allowed


def test_marketing_template_allowed_with_active_opt_in() -> None:
    decision = check(
        _ctx(
            template_category=TemplateCategory.MARKETING,
            template_name="abandoned_cart",
            has_active_opt_in=True,
        )
    )
    assert decision.allowed


# ── FR-037 (a) phone ────────────────────────────────────────────────


def test_no_phone_returns_no_phone_reason() -> None:
    assert check(_ctx(phone=None)).reason == SendSkipReason.NO_PHONE
    assert check(_ctx(phone="")).reason == SendSkipReason.NO_PHONE


@pytest.mark.parametrize(
    "bad_phone",
    [
        "201001234567",  # missing +
        "+abc",  # non-digits
        "+",  # only plus
        "+12345",  # too short (< 8 digits)
        "+1234567890123456",  # too long (> 15 digits)
    ],
)
def test_invalid_phone_shapes_rejected(bad_phone: str) -> None:
    assert check(_ctx(phone=bad_phone)).reason == SendSkipReason.INVALID_PHONE


# ── FR-037 (b) credentials ──────────────────────────────────────────


def test_no_credentials_returns_no_credentials() -> None:
    assert (
        check(_ctx(store_has_credentials=False)).reason == SendSkipReason.NO_CREDENTIALS
    )


def test_credentials_marked_invalid() -> None:
    """FR-025 — BYO credentials in error state must NOT silently fall
    back to platform; guard short-circuits."""
    assert (
        check(_ctx(store_credentials_marked_invalid=True)).reason
        == SendSkipReason.CREDENTIALS_INVALID
    )


# ── FR-037 (c) merchant notification setting ────────────────────────


def test_merchant_setting_off_blocks_normal_send() -> None:
    assert (
        check(_ctx(notification_setting_enabled=False)).reason
        == SendSkipReason.MERCHANT_SETTING_OFF
    )


def test_merchant_setting_off_does_not_block_bypass_allowlist() -> None:
    """The STOP-ack must send even if all toggles are off — it's a
    compliance-required confirmation."""
    decision = check(
        _ctx(
            template_name="optout_confirmation_ar",
            notification_setting_enabled=False,
            has_opt_out=True,
        )
    )
    assert decision.allowed


# ── FR-037 (d) explicit opt-out ─────────────────────────────────────


@pytest.mark.parametrize(
    "category",
    [
        TemplateCategory.UTILITY,
        TemplateCategory.AUTHENTICATION,
        TemplateCategory.MARKETING,
    ],
)
def test_explicit_opt_out_blocks_every_category(category: TemplateCategory) -> None:
    decision = check(
        _ctx(template_category=category, has_opt_out=True, has_active_opt_in=True)
    )
    assert decision.reason == SendSkipReason.OPT_OUT


# ── FR-037 (e) marketing opt-in requirement ─────────────────────────


def test_marketing_without_opt_in_blocked() -> None:
    decision = check(
        _ctx(
            template_category=TemplateCategory.MARKETING,
            template_name="abandoned_cart",
            has_active_opt_in=False,
        )
    )
    assert decision.reason == SendSkipReason.NO_OPT_IN


# ── FR-037 (f) 24h window for non-template sends ────────────────────


def test_non_template_send_blocked_when_window_closed() -> None:
    decision = check(
        _ctx(
            template_name=None,
            template_category=None,
            template_status=None,
            window_is_open=False,
        )
    )
    assert decision.reason == SendSkipReason.WINDOW_CLOSED


def test_template_send_ignores_window() -> None:
    """Templates can be sent at any time (Meta policy)."""
    decision = check(_ctx(window_is_open=False))
    assert decision.allowed


# ── FR-037 (g) template_not_approved (FR-029 / analyze finding C1) ──


@pytest.mark.parametrize(
    "status",
    ["PENDING", "REJECTED", "FLAGGED", "PAUSED", "DISABLED", None],
)
def test_non_approved_template_rejected(status: str | None) -> None:
    decision = check(_ctx(template_status=status))
    assert decision.reason == SendSkipReason.TEMPLATE_NOT_APPROVED


# ── Idempotency ─────────────────────────────────────────────────────


def test_already_sent_short_circuits_after_structural_guards() -> None:
    decision = check(_ctx(already_sent=True))
    assert decision.reason == SendSkipReason.ALREADY_SENT


def test_already_sent_does_not_apply_if_guard_already_failed() -> None:
    """If a structural guard fails first, that reason wins — already_sent
    is checked last so it doesn't mask real misconfiguration."""
    decision = check(_ctx(phone=None, already_sent=True))
    assert decision.reason == SendSkipReason.NO_PHONE


# ── Bypass allowlist ────────────────────────────────────────────────


def test_bypass_allowlist_constant_is_exactly_two_templates() -> None:
    """TASK-SEC-010 — defence in depth. Tests assert this set never
    grows without an explicit code change AND test update.
    """
    assert OPT_IN_BYPASS_ALLOWLIST == frozenset({
        "optout_confirmation_en",
        "optout_confirmation_ar",
    })


def test_bypass_allowlist_template_allowed_through_opt_out() -> None:
    decision = check(
        _ctx(
            template_name="optout_confirmation_en",
            has_opt_out=True,
            has_active_opt_in=False,
        )
    )
    assert decision.allowed


def test_non_allowlist_template_cannot_claim_bypass() -> None:
    """Any other template name with opt-out must be rejected — bypass is
    not a per-call switch."""
    decision = check(
        _ctx(
            template_name="random_marketing_template",
            template_category=TemplateCategory.MARKETING,
            has_opt_out=True,
        )
    )
    assert decision.reason == SendSkipReason.OPT_OUT
