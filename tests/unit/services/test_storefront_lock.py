"""Unit tests for the derived storefront billing lock."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.application.services.storefront_lock import (
    AWAITING_SUBSCRIPTION,
    AWAITING_TOPUP,
    PAYG_GATE_FROM,
    lock_password,
    lock_password_hash,
    lock_reason,
    resolve_lock_reason,
)

BEFORE_GATE = datetime(2026, 1, 1, tzinfo=UTC)
AFTER_GATE = PAYG_GATE_FROM + timedelta(days=1)

# Every lifecycle state the column actually holds. `past_due` is the one the
# first version of this module missed: it asked `not is_writable`, which is
# true for a subscriber mid-dunning, and shut their storefront.
WRITABLE_STATES = ("demo", "trial", "active")


def tenant(state, plan="starter", created=BEFORE_GATE):
    """A fake tenant whose two lifecycle flags cannot disagree.

    The originals set `is_writable` by hand and never mentioned
    `is_read_only`, so a fake could describe a tenant the database can
    never produce — which is how `past_due` slipped through review.
    """
    return SimpleNamespace(
        lifecycle_state=state,
        plan=plan,
        created_at=created,
        id="tenant-1",
        is_read_only=(state == "read_only"),
        is_writable=(state in WRITABLE_STATES),
    )


class Session:
    """Stands in for the wallet lookup in `resolve_lock_reason`."""

    def __init__(self, funded=False):
        self._funded = funded
        self.queried = False

    async def scalar(self, _query):
        self.queried = True
        return self._funded


def run(coro):
    return asyncio.run(coro)


class TestLockReason:
    def test_only_read_only_locks(self):
        for state in WRITABLE_STATES:
            assert lock_reason(tenant(state)) is None, state
            assert lock_reason(tenant(state, plan="payg")) is None, state

    def test_past_due_subscriber_keeps_their_storefront(self):
        """Dunning is mid-collection, not a decision to stop collecting.

        Regression: this locked vionne, a paying pro merchant, in production
        on 2026-09-07.
        """
        assert lock_reason(tenant("past_due", plan="pro")) is None

    def test_payg_is_told_to_top_up(self):
        assert lock_reason(tenant("read_only", plan="payg")) == AWAITING_TOPUP

    def test_other_plans_are_told_to_subscribe(self):
        for plan in ("starter", "growth", "PRO", ""):
            assert (
                lock_reason(tenant("read_only", plan=plan)) == AWAITING_SUBSCRIPTION
            ), plan

    def test_missing_tenant_reads_as_unlocked(self):
        """A data bug must not lock every storefront it touches."""
        assert lock_reason(None) is None


class TestResolveLockReason:
    def test_read_only_still_wins(self):
        got = run(
            resolve_lock_reason(Session(funded=True), tenant("read_only", plan="pro"))
        )
        assert got == AWAITING_SUBSCRIPTION

    def test_past_due_is_not_gated(self):
        assert (
            run(resolve_lock_reason(Session(), tenant("past_due", plan="pro"))) is None
        )

    def test_new_unfunded_payg_is_locked_even_while_active(self):
        t = tenant("active", plan="payg", created=AFTER_GATE)
        assert run(resolve_lock_reason(Session(funded=False), t)) == AWAITING_TOPUP

    def test_new_funded_payg_is_open(self):
        t = tenant("active", plan="payg", created=AFTER_GATE)
        assert run(resolve_lock_reason(Session(funded=True), t)) is None

    def test_payg_predating_the_gate_is_grandfathered(self):
        """Regression: this shut eight live storefronts on 2026-09-07."""
        t = tenant("active", plan="payg", created=BEFORE_GATE)
        assert run(resolve_lock_reason(Session(funded=False), t)) is None

    def test_payg_without_a_creation_date_is_left_alone(self):
        t = tenant("active", plan="payg", created=None)
        assert run(resolve_lock_reason(Session(funded=False), t)) is None

    def test_paid_plan_never_queries_the_wallet(self):
        """A starter tenant must not be locked for an empty wallet."""
        session = Session(funded=False)
        assert (
            run(resolve_lock_reason(session, tenant("active", plan="starter"))) is None
        )
        assert session.queried is False


class TestLockPassword:
    def test_stable_per_store_and_distinct_between_stores(self):
        a = "9f1c8b2e-0000-4000-8000-000000000001"
        b = "9f1c8b2e-0000-4000-8000-000000000002"
        assert lock_password(a) == lock_password(a)
        assert lock_password(a) != lock_password(b)

    def test_hash_matches_the_merchant_gate_scheme(self):
        store_id = "9f1c8b2e-0000-4000-8000-000000000003"
        expected = hashlib.sha256(lock_password(store_id).encode()).hexdigest()
        assert lock_password_hash(store_id) == expected

    def test_password_is_typeable(self):
        pwd = lock_password("9f1c8b2e-0000-4000-8000-000000000004")
        assert pwd.isalnum() and pwd.islower() and len(pwd) == 10
