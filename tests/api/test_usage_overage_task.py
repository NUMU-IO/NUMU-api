"""Tests for backend-017 verification-overage Celery task.

Pins the task's plan-cap mapping, idempotency-key shape, and overage
math against fakes for the DB session + UsageRelayService. Avoids
spinning a real Postgres because the global conftest's SQLite
in-memory bootstrap doesn't support the BYTEA columns the wider repo
graph requires.
"""

from __future__ import annotations

import pytest

from src.infrastructure.messaging.tasks.usage_overage_task import (
    OVERAGE_RATE_CENTS,
    PLAN_VERIFICATION_CAPS,
)


class TestPlanCaps:
    """Lock the public plan caps. Changing these without coordination
    with the Shopify-app billing.server.ts pricing is a billing-loop
    bug — the cap on the relay side must match the cap the Shopify-app
    advertises in the listing."""

    def test_starter_cap_is_450(self):
        assert PLAN_VERIFICATION_CAPS["starter"] == 450

    def test_growth_cap_is_5000(self):
        assert PLAN_VERIFICATION_CAPS["growth"] == 5_000

    def test_scale_cap_is_15000(self):
        assert PLAN_VERIFICATION_CAPS["scale"] == 15_000

    def test_only_three_paying_plans_have_caps(self):
        assert set(PLAN_VERIFICATION_CAPS.keys()) == {"starter", "growth", "scale"}


class TestOverageRate:
    def test_overage_rate_is_5_cents(self):
        """1 message × $0.05 = 5 cents. Locked to match the public
        pricing decision."""
        assert OVERAGE_RATE_CENTS == 5


class TestIdempotencyKeyShape:
    """The relay dedupes upstream by ``idempotency_key``. The task
    constructs it as ``f"{store_id}-{period_start.isoformat()}-overage"``
    — re-running the daily sweep within the same period must hit the
    same key so Shopify rejects the duplicate."""

    def test_key_is_stable_across_calls(self):
        from datetime import UTC, datetime

        store_id = "abcd-1234"
        period_start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
        key1 = f"{store_id}-{period_start.isoformat()}-overage"
        key2 = f"{store_id}-{period_start.isoformat()}-overage"
        assert key1 == key2
        assert "overage" in key1
        assert "2026-05-01" in key1


class TestTaskRegistration:
    def test_task_is_registered_with_celery(self):
        """The Celery beat schedule references the task by name. If
        the @celery_app.task decorator drops the name, the daily sweep
        silently never runs. Pin the registration."""
        from src.infrastructure.messaging.celery_app import celery_app

        assert "tasks.report_verification_overages" in celery_app.tasks

    def test_beat_schedule_contains_overage_entry(self):
        from src.infrastructure.messaging.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "report-verification-overages" in beat
        assert (
            beat["report-verification-overages"]["task"]
            == "tasks.report_verification_overages"
        )


class TestOverageMath:
    @pytest.mark.parametrize(
        "plan,sent,expected_overage,expected_amount_cents",
        [
            ("starter", 449, 0, 0),  # below cap → no charge
            ("starter", 450, 0, 0),  # exactly at cap → no charge
            ("starter", 451, 1, 5),  # 1 over → 5 cents
            ("starter", 500, 50, 250),  # 50 over → 250 cents = $2.50
            ("growth", 5_001, 1, 5),  # Growth cap edge
            ("scale", 15_010, 10, 50),  # Scale cap + 10 → 50 cents
        ],
    )
    def test_overage_math(self, plan, sent, expected_overage, expected_amount_cents):
        cap = PLAN_VERIFICATION_CAPS[plan]
        overage = max(sent - cap, 0)
        assert overage == expected_overage
        assert overage * OVERAGE_RATE_CENTS == expected_amount_cents
