"""NetworkOutcomeRecordedEvent handler — strict consent, disabled short-circuit, dedup.

The handler opens its own session and re-checks store consent, so it's exercised here
with the session / settings-repo / client all monkeypatched (no DB needed)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.core.events.network_events import NetworkOutcomeRecordedEvent
from src.infrastructure.events.handlers.network_feed_handler import (
    handle_network_outcome_recorded,
)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return None


class _FakeSettings:
    def __init__(self, enabled: bool):
        self.trust_network_enabled = enabled


def _wire(monkeypatch, *, enabled: bool, consented: bool):
    """Patch feed_config / session / settings-repo / client; return the captured sends."""
    sends: list[dict] = []

    monkeypatch.setattr(
        "src.application.services.trust_network_feed.feed_config",
        lambda: {"enabled": enabled, "url": "http://x", "api_key": "k", "timeout": 3.0},
    )
    monkeypatch.setattr(
        "src.infrastructure.database.connection.AsyncSessionLocal",
        lambda: _FakeSession(),
    )

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def get_or_create(self, store_id):
            return _FakeSettings(consented)

    monkeypatch.setattr(
        "src.infrastructure.repositories.shopify_repository.ShopifyAppSettingsRepository",
        _FakeRepo,
    )

    async def _fake_send(*, phone_hash, event_type, dedup_key):
        sends.append({
            "phone_hash": phone_hash,
            "event_type": event_type,
            "dedup_key": dedup_key,
        })
        return True

    monkeypatch.setattr(
        "src.application.services.trust_network_feed.send_network_outcome", _fake_send
    )
    return sends


def _event(dedup_key="store:order:rto"):
    return NetworkOutcomeRecordedEvent(
        store_id=uuid4(), phone_hash="tok_abc", event_type="rto", dedup_key=dedup_key
    )


def test_forwards_when_enabled_and_consented(monkeypatch):
    sends = _wire(monkeypatch, enabled=True, consented=True)
    asyncio.run(handle_network_outcome_recorded(_event()))
    assert sends == [
        {"phone_hash": "tok_abc", "event_type": "rto", "dedup_key": "store:order:rto"}
    ]


def test_skips_when_store_opted_out(monkeypatch):
    sends = _wire(monkeypatch, enabled=True, consented=False)
    asyncio.run(handle_network_outcome_recorded(_event()))
    assert sends == []  # strict consent — never posts for an opted-out store


def test_noop_when_feed_disabled(monkeypatch):
    sends = _wire(monkeypatch, enabled=False, consented=True)
    asyncio.run(handle_network_outcome_recorded(_event()))
    assert sends == []  # disabled → returns before any DB work or post


def test_dedup_key_falls_back_to_event_id(monkeypatch):
    sends = _wire(monkeypatch, enabled=True, consented=True)
    event = _event(dedup_key=None)
    asyncio.run(handle_network_outcome_recorded(event))
    assert sends[0]["dedup_key"] == f"numu:{event.event_id}"
