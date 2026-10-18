"""The onboarding nudge task runs, and sends each email once.

It imported ``src.infrastructure.messaging.redis_client``, a module that never
existed, so it had never sent: the worker log shows the ModuleNotFoundError on
every run (2026-10-15..18). Its trial-warning sibling was removed instead of
fixed: ``tasks.send_subscription_expiry_warnings`` already sends trial stores a
bilingual warning, and the sibling's copy was English only.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infrastructure.cache import redis_cache
from src.infrastructure.database import connection
from src.infrastructure.external_services.resend import email_service
from src.infrastructure.messaging.tasks import onboarding_nudge_tasks as nudges


class _Redis:
    def __init__(self):
        self.keys = {}

    async def exists(self, key):
        return int(key in self.keys)

    async def set(self, key, value, ex=None):
        self.keys[key] = value


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _statement):
        return SimpleNamespace(
            all=lambda: self.rows,
            scalars=lambda: SimpleNamespace(all=lambda: self.rows),
        )


@pytest.fixture
def outbox(monkeypatch):
    redis = _Redis()
    sent = []

    class _Cache:
        async def _get_client(self):
            return redis

    class _Email:
        async def send_email(self, message):
            sent.append(message.to)
            return True

    monkeypatch.setattr(redis_cache, "RedisCacheService", _Cache)
    monkeypatch.setattr(email_service, "ResendEmailService", _Email)
    return sent


def test_no_product_nudge_goes_out_once(monkeypatch, outbox):
    user = SimpleNamespace(
        email="owner@example.com", first_name="Mona", trial_ends_at=None
    )
    store = SimpleNamespace(
        id=uuid4(),
        name="Test Store",
        default_language="ar",
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    monkeypatch.setattr(
        connection, "AsyncSessionLocal", lambda: _Session([(user, store)])
    )

    assert nudges.send_inactive_merchant_nudges.run() == {"sent": 1}
    assert nudges.send_inactive_merchant_nudges.run() == {"sent": 0}
    assert outbox == ["owner@example.com"]
