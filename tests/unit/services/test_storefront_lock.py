"""Unit tests for the derived storefront billing lock."""

from types import SimpleNamespace

from src.application.services.storefront_lock import (
    AWAITING_SUBSCRIPTION,
    AWAITING_TOPUP,
    lock_password,
    lock_password_hash,
    lock_reason,
)


def _tenant(*, writable: bool, plan: str = "starter"):
    return SimpleNamespace(is_writable=writable, plan=plan)


class TestLockReason:
    def test_writable_tenant_is_not_locked(self):
        assert lock_reason(_tenant(writable=True)) is None
        assert lock_reason(_tenant(writable=True, plan="payg")) is None

    def test_payg_is_told_to_top_up(self):
        assert lock_reason(_tenant(writable=False, plan="payg")) == AWAITING_TOPUP

    def test_other_plans_are_told_to_subscribe(self):
        for plan in ("starter", "growth", "PRO", ""):
            got = lock_reason(_tenant(writable=False, plan=plan))
            assert got == AWAITING_SUBSCRIPTION, plan

    def test_missing_tenant_reads_as_unlocked(self):
        """A data bug must not lock every storefront it touches."""
        assert lock_reason(None) is None


class TestLockPassword:
    def test_stable_per_store_and_distinct_between_stores(self):
        a = "9f1c8b2e-0000-4000-8000-000000000001"
        b = "9f1c8b2e-0000-4000-8000-000000000002"
        assert lock_password(a) == lock_password(a)
        assert lock_password(a) != lock_password(b)

    def test_hash_matches_the_merchant_gate_scheme(self):
        import hashlib

        store_id = "9f1c8b2e-0000-4000-8000-000000000003"
        expected = hashlib.sha256(lock_password(store_id).encode()).hexdigest()
        assert lock_password_hash(store_id) == expected

    def test_password_is_typeable(self):
        pwd = lock_password("9f1c8b2e-0000-4000-8000-000000000004")
        assert pwd.isalnum() and pwd.islower() and len(pwd) == 10
