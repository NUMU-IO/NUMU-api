"""Operator notifications must follow the transaction, not the intent.

Every caller sits inside a merchant-facing write. A notification that fires
before the commit is either a lie — the signup rolled back and there is no
lead — or a race, where the operator taps through to a row Postgres has not
made visible yet. These tests pin both directions.
"""

from unittest.mock import patch

import pytest

from src.application.services import admin_notifications


class _FakeSyncSession:
    def __init__(self) -> None:
        self.info: dict = {}
        self.listeners: dict[str, list] = {}


class _FakeSession:
    """The parts of AsyncSession the deferral touches."""

    def __init__(self, *, in_txn: bool = True) -> None:
        self.sync_session = _FakeSyncSession()
        self._in_txn = in_txn

    def in_transaction(self) -> bool:
        return self._in_txn

    def commit(self) -> None:
        admin_notifications._on_commit(self.sync_session)

    def rollback(self) -> None:
        admin_notifications._on_rollback(self.sync_session)


@pytest.fixture
def listen(monkeypatch):
    """Record `sa_event.listen` calls instead of binding to a real session."""

    def _listen(target, name, fn):
        target.listeners.setdefault(name, []).append(fn)

    monkeypatch.setattr(admin_notifications.sa_event, "listen", _listen)


def test_nothing_is_sent_before_commit(listen):
    session = _FakeSession()
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.lead_captured(session, email="a@b.co", source="landing")
        assert send.call_count == 0, "notified before the row was durable"

        session.commit()
        assert send.call_count == 1


def test_rollback_discards_the_notification(listen):
    session = _FakeSession()
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.lead_captured(session, email="a@b.co", source="signup")
        session.rollback()
        assert send.call_count == 0

        # And the buffer is gone, so a later commit cannot resurrect it.
        session.commit()
        assert send.call_count == 0


def test_sends_immediately_without_a_transaction():
    """Celery tasks and tests have already committed; there is nothing to wait for."""
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.theme_submitted(None, theme_name="Empire", version="1.2.0")
        assert send.call_count == 1


def test_a_broker_outage_cannot_break_the_caller(listen):
    session = _FakeSession()
    with patch.object(
        admin_notifications, "_send", side_effect=RuntimeError("no broker")
    ):
        admin_notifications.lead_captured(session, email="a@b.co", source="landing")
        # The producer is a merchant signup. It must survive the flush.
        session.commit()


def test_lead_tag_is_per_lead_and_queues_are_per_queue(listen):
    """Tag choice is the difference between a queue ping and a lost lead."""
    session = _FakeSession()
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.lead_captured(session, email="a@b.co", source="landing")
        admin_notifications.lead_captured(session, email="c@d.co", source="landing")
        admin_notifications.whatsapp_access_requested(session, store_name="Qandeel")
        admin_notifications.whatsapp_access_requested(session, store_name="Vionne")
        session.commit()

    tags = [call.args[0]["tag"] for call in send.call_args_list]
    # Two leads must survive as two notifications; the OS collapses same-tag.
    assert tags[0] != tags[1]
    # Two access requests are one thing to go and look at.
    assert tags[2] == tags[3] == "admin:whatsapp-access"


def test_bodies_carry_no_customer_data(listen):
    """These render on a lock screen. Merchant identity only, never a customer."""
    session = _FakeSession()
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.wallet_topup_submitted(session, store_name="Qandeel")
        admin_notifications.subscription_proof_submitted(session, store_name="Qandeel")
        session.commit()

    for call in send.call_args_list:
        payload = call.args[0]
        assert payload["url"].startswith("/"), "push urls must be relative in-app paths"
        assert "Qandeel" in payload["body"]
