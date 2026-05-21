"""Tests for backend-014 trust network correctness.

Covers the four claims that turn the network from scaffold-only into
load-bearing:

  1. Production startup requires ``PLATFORM_SECRET_SALT`` (no silent
     fail-open).
  2. ``write_network_event`` honors the per-store ``trust_network_enabled``
     opt-in flag.
  3. ``customers/redact`` decrements network signal by phone hash, not
     just by email.
  4. ``customers/data_request`` includes network contributions in the
     export when phone is provided.

The repository methods that hit Postgres types incompatible with SQLite
(the global conftest fixture's in-memory bootstrap) are exercised
through fakes; only the consent + DSAR control flow needs validation
here, not the SQL itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.application.services.network_reputation_service import (
    write_network_event,
)

# ─────────────────────────────────────────────────────────────────────
# 1. Production salt enforcement
# ─────────────────────────────────────────────────────────────────────


class TestProductionSaltRequired:
    def test_production_without_salt_raises(self, monkeypatch):
        """Missing PLATFORM_SECRET_SALT in production should crash on
        boot, not silently fail open. The previous behavior — empty salt
        → phone_hash always None → empty network table — was the killer
        the audit named."""
        from pydantic import ValidationError

        from src.config.settings import Settings

        # Minimum valid env for prod EXCEPT salt.
        env = {
            "ENVIRONMENT": "production",
            "DEBUG": "false",
            "SESSION_SECRET_KEY": "x" * 64,
            "CORS_ORIGINS": '["https://numueg.app"]',
            "PLATFORM_SECRET_SALT": "",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises((ValueError, ValidationError)) as exc_info:
            Settings()  # type: ignore[call-arg]
        assert "PLATFORM_SECRET_SALT" in str(exc_info.value)

    def test_development_without_salt_does_not_raise(self, monkeypatch):
        """Dev/test environments still allow empty salt — the network
        is best-effort there."""
        from src.config.settings import Settings

        env = {
            "ENVIRONMENT": "development",
            "PLATFORM_SECRET_SALT": "",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        # Should NOT raise.
        s = Settings()  # type: ignore[call-arg]
        assert s.environment == "development"


# ─────────────────────────────────────────────────────────────────────
# 2. Per-store trust_network_enabled consent
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _FakeSettings:
    trust_network_enabled: bool = True


class _FakeSettingsRepo:
    """Records get_or_create calls + returns scripted settings."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.calls: list[UUID] = []

    async def get_or_create(
        self, store_id: UUID, tenant_id: UUID | None = None
    ) -> _FakeSettings:
        self.calls.append(store_id)
        return _FakeSettings(trust_network_enabled=self.enabled)


@dataclass
class _RecordingNetworkRepo:
    """Records every method call so we can assert the write was/wasn't made."""

    upsert_order_calls: list[dict] = field(default_factory=list)
    record_event_calls: list[dict] = field(default_factory=list)
    update_store_count_calls: list[str] = field(default_factory=list)
    recompute_calls: list[str] = field(default_factory=list)

    async def upsert_order(self, *, phone_hash: str, store_id: UUID) -> Any:
        self.upsert_order_calls.append({"phone_hash": phone_hash, "store_id": store_id})

    async def record_event(
        self, *, phone_hash: str, store_id: UUID, event_type: str
    ) -> None:
        self.record_event_calls.append({
            "phone_hash": phone_hash,
            "store_id": store_id,
            "event_type": event_type,
        })

    async def update_store_count(self, phone_hash: str) -> None:
        self.update_store_count_calls.append(phone_hash)

    async def recompute_cached_score(self, phone_hash: str) -> None:
        self.recompute_calls.append(phone_hash)


class TestConsentEnforcement:
    @pytest.mark.asyncio
    async def test_opted_in_store_writes_event(self):
        net_repo = _RecordingNetworkRepo()
        settings_repo = _FakeSettingsRepo(enabled=True)
        store = uuid4()

        await write_network_event(
            phone_hash="abc123",
            store_id=store,
            event_type="order",
            network_repo=net_repo,  # type: ignore[arg-type]
            settings_repo=settings_repo,  # type: ignore[arg-type]
        )

        assert net_repo.upsert_order_calls == [
            {"phone_hash": "abc123", "store_id": store}
        ]
        assert settings_repo.calls == [store]

    @pytest.mark.asyncio
    async def test_opted_out_store_skips_write(self):
        """The strategic claim: if a merchant opts out, NO trace of their
        order leaks into the cross-merchant network. Verifying this means
        verifying every network-write method stays uncalled."""
        net_repo = _RecordingNetworkRepo()
        settings_repo = _FakeSettingsRepo(enabled=False)
        store = uuid4()

        await write_network_event(
            phone_hash="abc123",
            store_id=store,
            event_type="order",
            network_repo=net_repo,  # type: ignore[arg-type]
            settings_repo=settings_repo,  # type: ignore[arg-type]
        )

        assert net_repo.upsert_order_calls == []
        assert net_repo.record_event_calls == []
        assert net_repo.update_store_count_calls == []
        assert net_repo.recompute_calls == []
        # The settings WERE consulted — that's the consent check itself.
        assert settings_repo.calls == [store]

    @pytest.mark.asyncio
    async def test_no_settings_repo_logs_bypass_but_still_writes(self, caplog):
        """Legacy callers that don't pass a settings_repo emit an audit
        log line and still write — backwards-compat, but every bypass is
        traceable. This is the path the native storefront takes today."""
        import logging

        caplog.set_level(logging.INFO)
        net_repo = _RecordingNetworkRepo()
        store = uuid4()

        await write_network_event(
            phone_hash="abc123",
            store_id=store,
            event_type="rto",
            network_repo=net_repo,  # type: ignore[arg-type]
            settings_repo=None,
        )

        assert net_repo.record_event_calls == [
            {"phone_hash": "abc123", "store_id": store, "event_type": "rto"}
        ]
        assert any(
            "network_event_consent_check_bypassed" in rec.message
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_none_phone_hash_short_circuits_before_consent_check(self):
        """The existing salt-fail-open guard runs first — if there's no
        phone hash, we don't even consult settings. This preserves the
        legacy behavior: missing salt → no-op without leaking the consent
        check to a non-existent store record."""
        net_repo = _RecordingNetworkRepo()
        settings_repo = _FakeSettingsRepo(enabled=True)

        await write_network_event(
            phone_hash=None,
            store_id=uuid4(),
            event_type="order",
            network_repo=net_repo,  # type: ignore[arg-type]
            settings_repo=settings_repo,  # type: ignore[arg-type]
        )

        assert net_repo.upsert_order_calls == []
        assert settings_repo.calls == []  # never consulted


# ─────────────────────────────────────────────────────────────────────
# 3 + 4. DSAR completeness — covered by signature-level smoke tests
# ─────────────────────────────────────────────────────────────────────
#
# The actual SQL for delete_customer_network_data + list_customer_contributions
# requires PostgreSQL features (BYTEA, advisory locks via the wider repo
# graph) that the SQLite-based test fixture doesn't support. These two
# methods are smoke-tested here to ensure they exist with the documented
# signatures and import cleanly; their behavior is exercised by the
# repository's existing PostgreSQL integration test suite.


class TestDsarApiSurface:
    def test_delete_customer_network_data_exists(self):
        from src.infrastructure.repositories.shopify_repository import (
            NetworkReputationRepository,
        )

        method = NetworkReputationRepository.delete_customer_network_data
        assert method is not None
        # Signature: (self, *, store_id: UUID, phone_hash: str) -> dict
        import inspect

        sig = inspect.signature(method)
        params = sig.parameters
        assert "store_id" in params
        assert "phone_hash" in params
        # Both must be keyword-only — the method is destructive enough
        # that positional misuse should be a type error.
        assert params["store_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["phone_hash"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_list_customer_contributions_exists(self):
        from src.infrastructure.repositories.shopify_repository import (
            NetworkReputationRepository,
        )

        method = NetworkReputationRepository.list_customer_contributions
        assert method is not None
        import inspect

        sig = inspect.signature(method)
        params = sig.parameters
        assert "store_id" in params
        assert "phone_hash" in params

    def test_shopify_app_settings_has_trust_network_enabled(self):
        """The opt-in column is the GDPR Recital 47 control surface.
        Removing it would silently make every store opted-in by default
        with no way to opt out — a regression worth pinning."""
        from src.infrastructure.database.models.tenant.shopify_app_settings import (
            ShopifyAppSettingsModel,
        )

        column = ShopifyAppSettingsModel.__table__.columns.get("trust_network_enabled")
        assert column is not None
        assert column.nullable is False
        # Default true — opt-in at install via disclosure modal.
        assert "true" in str(column.server_default.arg)
