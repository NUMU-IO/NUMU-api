"""Operator notifications must follow the transaction, not the intent.

Every caller sits inside a merchant-facing write. A notification that fires
before the commit is either a lie — the signup rolled back and there is no
lead — or a race, where the operator taps through to a row Postgres has not
made visible yet. These tests pin both directions.
"""

from unittest.mock import patch

import pytest

from src.application.services import admin_notifications


def _settings_returning(value: dict):
    async def _get(_session):
        return value

    return _get


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


# ── Both channels ────────────────────────────────────────────────────────────
# Push reaches registered devices only. Production had zero platform device
# registrations, so every notification since this feature shipped — four leads
# in the day this was found — fanned out to an empty audience and nobody
# noticed, because a fan-out to nobody is indistinguishable from silence.


def test_every_notification_goes_out_by_push_and_email(monkeypatch, listen):
    pushed: list[dict] = []
    mailed: list[dict] = []
    monkeypatch.setattr(
        "src.infrastructure.messaging.tasks.push_tasks.notify_admins",
        lambda **kw: pushed.append(kw),
    )
    monkeypatch.setattr(
        "src.infrastructure.messaging.tasks.admin_alert_email_task.email_admins",
        lambda **kw: mailed.append(kw),
    )

    session = _FakeSession()
    admin_notifications.lead_captured(session, email="a@b.co", source="landing")
    session.commit()

    assert len(pushed) == len(mailed) == 1
    assert mailed[0]["title"] == "New merchant lead"
    assert mailed[0]["url"] == "/leads"
    # `tag` is an OS-level collapsing key with no meaning in an inbox.
    assert "tag" not in mailed[0]


def test_a_dead_email_task_still_lets_the_push_through(monkeypatch, listen):
    """The two channels must not be able to take each other down."""
    pushed: list[dict] = []
    monkeypatch.setattr(
        "src.infrastructure.messaging.tasks.push_tasks.notify_admins",
        lambda **kw: pushed.append(kw),
    )
    monkeypatch.setattr(
        "src.infrastructure.messaging.tasks.admin_alert_email_task.email_admins",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("resend down")),
    )

    session = _FakeSession()
    admin_notifications.lead_captured(session, email="a@b.co", source="landing")
    session.commit()  # must not raise — this runs inside the caller's commit

    assert len(pushed) == 1


def test_milestones_announce_once_and_only_the_ones_worth_a_ping(listen):
    session = _FakeSession()
    with patch.object(admin_notifications, "_send") as send:
        admin_notifications.lead_advanced(session, email="a@b.co", status="registered")
        admin_notifications.lead_advanced(
            session, email="a@b.co", status="store_created"
        )
        # Not milestones an operator acts on: no notification at all.
        admin_notifications.lead_advanced(
            session, email="a@b.co", status="demo_started"
        )
        admin_notifications.lead_advanced(session, email="a@b.co", status="activated")
        session.commit()

    titles = [call.args[0]["title"] for call in send.call_args_list]
    assert titles == ["Merchant registered", "Store created"]


@pytest.mark.asyncio
async def test_the_email_links_somewhere_a_browser_can_open(monkeypatch):
    """The notification's `url` is a relative admin path — fine for a service
    worker, which resolves it against its own origin, and useless in an inbox."""
    from src.infrastructure.messaging.tasks import admin_alert_email_task as task

    sent: list = []

    class _Resend:
        async def send_email(self, message):
            sent.append(message)
            return True

    monkeypatch.setattr(
        "src.infrastructure.external_services.resend.email_service.ResendEmailService",
        _Resend,
    )
    monkeypatch.setattr(
        "src.api.v1.routes.admin.platform_settings.get_platform_settings",
        _settings_returning({"alert_emails": ["ops@numueg.app"]}),
    )

    out = await task._send(
        title="New merchant lead", body="a@b.co", url="/leads", important=False
    )

    assert out["status"] == "sent"
    assert sent[0].to == ["ops@numueg.app"]
    assert f'href="{task.ADMIN_ORIGIN}/leads"' in sent[0].html_content


@pytest.mark.asyncio
async def test_clearing_the_list_turns_the_emails_off_quietly(monkeypatch):
    """An empty recipient list is how an operator opts out — not an error to
    raise on every lead that arrives afterwards."""
    from src.infrastructure.messaging.tasks import admin_alert_email_task as task

    monkeypatch.setattr(
        "src.api.v1.routes.admin.platform_settings.get_platform_settings",
        _settings_returning({"alert_emails": []}),
    )

    out = await task._send(title="t", body="b", url="/leads", important=False)
    assert out == {"status": "skipped", "reason": "no_recipients"}


def test_yahya_is_the_default_recipient():
    """Shipping with an empty default would deploy the fix and change nothing."""
    from src.api.v1.routes.admin.platform_settings import DEFAULTS

    assert DEFAULTS["alert_emails"] == ["yahya@numueg.app"]
