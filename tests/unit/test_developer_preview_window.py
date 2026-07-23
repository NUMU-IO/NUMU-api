"""Developer preview window — the ADR-6 self-install bypass fix.

The bypass: `developer-install` deliberately installs a version that has NOT
been reviewed so a theme author can iterate on their own store, and
`activate_theme` never re-checked review status. Unreviewed third-party
JavaScript could therefore serve real shoppers permanently.

The fix time-boxes it. These tests pin the three things that make that real.
"""

from datetime import UTC, datetime, timedelta

from src.application.services.marketplace_service import DEVELOPER_PREVIEW_WINDOW
from src.core.entities.marketplace_theme import MarketplaceThemeInstallation
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeInstallationModel,
)


class TestPreviewWindowLength:
    def test_window_is_bounded_and_short(self):
        # A window long enough to be indistinguishable from "permanent" would
        # defeat the point of having one.
        assert timedelta(0) < DEVELOPER_PREVIEW_WINDOW <= timedelta(days=7)


class TestExpiryPredicate:
    def _model(self, expires_at):
        m = MarketplaceThemeInstallationModel()
        m.preview_expires_at = expires_at
        return m

    def test_no_window_never_expires(self):
        # An ordinary install of a PUBLISHED version carries no window and
        # must never be treated as expired.
        assert self._model(None).is_expired_preview is False

    def test_future_window_is_live(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert self._model(future).is_expired_preview is False

    def test_past_window_is_expired(self):
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert self._model(past).is_expired_preview is True


class TestEntityCarriesTheWindow:
    def test_default_is_none(self):
        # Defaulting to None keeps every pre-existing install unaffected.
        inst = MarketplaceThemeInstallation(
            store_id="11111111-1111-1111-1111-111111111111",
            marketplace_theme_id="22222222-2222-2222-2222-222222222222",
            marketplace_version_id="33333333-3333-3333-3333-333333333333",
        )
        assert inst.preview_expires_at is None

    def test_window_round_trips(self):
        expires = datetime.now(UTC) + DEVELOPER_PREVIEW_WINDOW
        inst = MarketplaceThemeInstallation(
            store_id="11111111-1111-1111-1111-111111111111",
            marketplace_theme_id="22222222-2222-2222-2222-222222222222",
            marketplace_version_id="33333333-3333-3333-3333-333333333333",
            preview_expires_at=expires,
        )
        assert inst.preview_expires_at == expires
