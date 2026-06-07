"""Unit tests for the kill-switch merchant-notification compose logic (P1-3)."""

from __future__ import annotations

from uuid import uuid4

from src.core.events.risk_events import TrustKillSwitchFiredEvent
from src.infrastructure.events.handlers.trust_kill_switch_notification_handler import (
    _compose,
)


def _event(
    reason: str = "3 of the last 20 auto-approved orders were returned (15.0%).",
) -> TrustKillSwitchFiredEvent:
    return TrustKillSwitchFiredEvent(
        store_id=uuid4(),
        tenant_id=uuid4(),
        auto_approve_count=20,
        rto_count=3,
        rate_pct=15.0,
        reason=reason,
    )


def test_compose_english_includes_store_reason_and_reenable_hint():
    subject, html = _compose("en", "Acme Store", _event())
    assert "auto-approve" in subject.lower()
    assert "Acme Store" in html
    assert "returned (15.0%)" in html
    assert "re-enable" in html.lower()


def test_compose_arabic_includes_reason_and_store():
    reason = "3 of the last 20 auto-approved orders were returned (15.0%)."
    subject, html = _compose("ar", "متجر النور", _event(reason))
    assert subject.strip()  # non-empty Arabic subject
    assert reason in html
    assert "متجر النور" in html
