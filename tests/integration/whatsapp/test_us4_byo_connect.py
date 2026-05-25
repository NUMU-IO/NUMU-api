"""Integration tests for US4 — BYO Meta WABA connect / disconnect.

Covers acceptance scenarios:
- AS-1 (T066): valid creds → 201, mode=byo, toggles all DISABLED,
  prior platform-managed toggle state snapshotted.
- AS-2 (T067): each of the 3 validation steps can fail; the failing
  step is identified in the 422 response.
- AS-3 (T068): disconnect → mode=platform_managed, prior toggle state
  restored.
- AS-4 (T069): BYO credentials expire at Meta → send fails LOUD; no
  silent fallback to platform creds (FR-025).
- AS-5 (T071): TASK-SEC-009 — Meta error body whitelist drops
  fbtrace_id and other internal fields.

T070 (rate limit) is deferred to the polish phase pending rate-limit
middleware integration.

Gated on ``NUMU_RUN_INTEGRATION_TESTS=1``.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.application.use_cases.whatsapp.connect_byo_credentials import (
    BYOValidationError,
    ConnectBYOCredentialsUseCase,
)
from src.application.use_cases.whatsapp.disconnect_byo_credentials import (
    DisconnectBYOCredentialsUseCase,
)
from src.core.services.meta_error_whitelist import sanitize_meta_error

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason="DB-backed integration tests; set NUMU_RUN_INTEGRATION_TESTS=1.",
)


def _happy_meta_client_mock(*, waba_id: str = "WABA_HAPPY"):
    """Build an AsyncMock matching ``WhatsAppClient`` with all 3 read
    methods returning happy-path bodies."""
    mock = MagicMock()
    mock.get_phone_number_info = AsyncMock(
        return_value={
            "verified_name": "Test BYO Store",
            "display_phone_number": "+201001234567",
            "quality_rating": "GREEN",
            "code_verification_status": "VERIFIED",
        }
    )
    mock.get_waba_info = AsyncMock(return_value={"id": waba_id, "name": "Test WABA"})
    mock.list_templates = AsyncMock(return_value={"data": []})
    mock.close = AsyncMock()
    return mock


# ── T066 / AS-1 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_byo_connect_happy_path_validates_and_persists(db_session, seeded_store):
    """Valid creds, all 3 reads succeed → row written + toggles reset."""
    # Seed prior platform-managed toggle state so the snapshot path
    # exercises.
    seeded_store.settings = dict(seeded_store.settings or {})
    seeded_store.settings["whatsapp_notifications"] = {
        "order_confirmation": True,
        "payment_received": True,
        "shipping_update": True,
        "delivery_confirmation": True,
        "abandoned_cart": True,
        "marketing": False,
    }
    await db_session.commit()

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=_happy_meta_client_mock(waba_id="WABA_001"),
    ):
        use_case = ConnectBYOCredentialsUseCase(db_session)
        result = await use_case.execute(
            store_id=seeded_store.id,
            access_token="EAA_fake_token",
            phone_number_id="111222333",
            waba_id="WABA_001",
            app_secret="appsecret_fake",
        )
        await db_session.commit()

    assert result["mode"] == "byo"
    assert result["waba_id"] == "WABA_001"

    # All notification toggles reset to False per FR-019a
    for key in ("order_confirmation", "payment_received", "marketing"):
        assert result["notifications"][key] is False

    # Prior state snapshotted
    await db_session.refresh(seeded_store)
    snap = seeded_store.settings.get("whatsapp_notifications_prev_platform_managed")
    assert snap is not None
    assert snap["order_confirmation"] is True


# ── T067 / AS-2 — each failing step ─────────────────────────────────


@pytest.mark.asyncio
async def test_byo_connect_phone_metadata_read_failure(db_session, seeded_store):
    """Step 1 fails → failed_step='phone_metadata_read', no row written."""
    client_mock = _happy_meta_client_mock()
    client_mock.get_phone_number_info = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "401",
            request=MagicMock(),
            response=MagicMock(
                json=MagicMock(
                    return_value={
                        "error": {
                            "code": 190,
                            "message": "Invalid OAuth access token",
                            "type": "OAuthException",
                            "fbtrace_id": "SHOULD_NOT_LEAK",
                        }
                    }
                )
            ),
        )
    )

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=client_mock,
    ):
        use_case = ConnectBYOCredentialsUseCase(db_session)
        with pytest.raises(BYOValidationError) as exc_info:
            await use_case.execute(
                store_id=seeded_store.id,
                access_token="EAA_bad",
                phone_number_id="x",
                waba_id="y",
                app_secret="z",
            )

    assert exc_info.value.failed_step == "phone_metadata_read"
    assert exc_info.value.code == "phone_number_unreachable"
    # TASK-SEC-009 — fbtrace_id must NOT appear in the surfaced error
    if exc_info.value.meta_error:
        assert "fbtrace_id" not in exc_info.value.meta_error
        assert exc_info.value.meta_error.get("code") == 190


@pytest.mark.asyncio
async def test_byo_connect_waba_info_read_failure_missing_scope(
    db_session, seeded_store
):
    """Step 2 fails (token lacks management scope) → 'insufficient_scope'."""
    client_mock = _happy_meta_client_mock()
    client_mock.get_waba_info = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "403",
            request=MagicMock(),
            response=MagicMock(
                json=MagicMock(
                    return_value={
                        "error": {
                            "code": 100,
                            "message": "Missing whatsapp_business_management scope",
                            "type": "OAuthException",
                        }
                    }
                )
            ),
        )
    )

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=client_mock,
    ):
        use_case = ConnectBYOCredentialsUseCase(db_session)
        with pytest.raises(BYOValidationError) as exc_info:
            await use_case.execute(
                store_id=seeded_store.id,
                access_token="EAA_no_management",
                phone_number_id="111",
                waba_id="WABA_002",
                app_secret="z",
            )

    assert exc_info.value.failed_step == "waba_info_read"
    assert exc_info.value.code == "insufficient_scope"


@pytest.mark.asyncio
async def test_byo_connect_waba_id_mismatch(db_session, seeded_store):
    """Step 2 returns a different waba_id than the one supplied → mismatch."""
    client_mock = _happy_meta_client_mock()
    client_mock.get_waba_info = AsyncMock(return_value={"id": "WABA_DIFFERENT"})

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=client_mock,
    ):
        use_case = ConnectBYOCredentialsUseCase(db_session)
        with pytest.raises(BYOValidationError) as exc_info:
            await use_case.execute(
                store_id=seeded_store.id,
                access_token="EAA_x",
                phone_number_id="111",
                waba_id="WABA_EXPECTED",
                app_secret="z",
            )

    assert exc_info.value.failed_step == "waba_info_read"
    assert exc_info.value.code == "waba_mismatch"


@pytest.mark.asyncio
async def test_byo_connect_template_list_read_failure(db_session, seeded_store):
    """Step 3 fails (phone doesn't belong to WABA, etc.) → 'waba_mismatch'."""
    client_mock = _happy_meta_client_mock(waba_id="WABA_003")
    client_mock.list_templates = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "404",
            request=MagicMock(),
            response=MagicMock(
                json=MagicMock(
                    return_value={
                        "error": {
                            "code": 100,
                            "message": "WABA not found or token cannot list templates",
                            "type": "OAuthException",
                        }
                    }
                )
            ),
        )
    )

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=client_mock,
    ):
        use_case = ConnectBYOCredentialsUseCase(db_session)
        with pytest.raises(BYOValidationError) as exc_info:
            await use_case.execute(
                store_id=seeded_store.id,
                access_token="EAA_x",
                phone_number_id="111",
                waba_id="WABA_003",
                app_secret="z",
            )

    assert exc_info.value.failed_step == "template_list_read"
    assert exc_info.value.code == "waba_mismatch"


# ── T068 / AS-3 — disconnect restores prior toggles ─────────────────


@pytest.mark.asyncio
async def test_byo_disconnect_restores_prior_toggle_state(db_session, seeded_store):
    """Connect → toggles reset to DISABLED + snapshot stored. Disconnect
    → toggles restored from snapshot."""
    seeded_store.settings = dict(seeded_store.settings or {})
    seeded_store.settings["whatsapp_notifications"] = {
        "order_confirmation": True,
        "payment_received": False,  # merchant had this off pre-BYO
        "shipping_update": True,
        "delivery_confirmation": True,
        "abandoned_cart": True,
        "marketing": False,
    }
    await db_session.commit()

    with patch(
        "src.application.use_cases.whatsapp.connect_byo_credentials.WhatsAppClient",
        return_value=_happy_meta_client_mock(),
    ):
        await ConnectBYOCredentialsUseCase(db_session).execute(
            store_id=seeded_store.id,
            access_token="t",
            phone_number_id="p",
            waba_id="WABA_HAPPY",
            app_secret="s",
        )

    # After connect → all toggles DISABLED
    await db_session.refresh(seeded_store)
    assert all(
        v is False for v in seeded_store.settings["whatsapp_notifications"].values()
    )

    # Disconnect → restored, including the order_confirmation=True and
    # payment_received=False
    result = await DisconnectBYOCredentialsUseCase(db_session).execute(
        store_id=seeded_store.id
    )
    assert result["mode"] == "platform_managed"
    assert result["notifications"]["order_confirmation"] is True
    assert result["notifications"]["payment_received"] is False


@pytest.mark.asyncio
async def test_byo_disconnect_with_no_prior_snapshot_defaults_all_true(
    db_session, seeded_store
):
    """When no prior snapshot exists (e.g., a store that connected BYO
    first thing), disconnect defaults the notification toggles to all-True
    (the platform-managed safe default per FR-019a)."""
    # Pre-seed a BYO ServiceCredential row directly (skip the connect
    # path that would auto-snapshot).
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )

    db_session.add(
        ServiceCredential(
            tenant_id=seeded_store.tenant_id,
            service_type=ServiceType.WHATSAPP,
            service_name=ServiceName.WHATSAPP_BUSINESS,
            credentials_encrypted="(fake)",
            encryption_key_id="(fake)",
            is_active=True,
            is_validated=True,
        )
    )
    await db_session.commit()

    result = await DisconnectBYOCredentialsUseCase(db_session).execute(
        store_id=seeded_store.id
    )
    assert result["mode"] == "platform_managed"
    assert result["notifications"]["order_confirmation"] is True
    assert result["notifications"]["marketing"] is False  # marketing default


# ── T069 / AS-4 — BYO credential failure must not silently fall back ─


@pytest.mark.asyncio
async def test_byo_credential_failure_fails_loud_no_platform_fallback(
    db_session, seeded_store_with_byo_credential_error
):
    """FR-025 — when the BYO credentials are in error state (e.g., token
    expired at Meta and the messaging service marked credential_error on
    the store), the send guard must SHORT-CIRCUIT with reason
    CREDENTIALS_INVALID rather than silently falling back to platform.

    The store-level `credential_error` field is set by the messaging
    service on the first credential-class Meta error; the guard reads
    `store.settings.whatsapp.credential_error` via GuardContext.
    """
    from src.core.enums.whatsapp import SendSkipReason, TemplateCategory
    from src.core.services.whatsapp_send_guard import GuardContext, check

    # Mirror the message service's expectation: settings.whatsapp.credential_error
    # is a non-empty string indicating the failure.
    assert seeded_store_with_byo_credential_error.settings["whatsapp"][
        "credential_error"
    ]

    ctx = GuardContext(
        phone="+201001234567",
        template_name="order_confirmation",
        template_category=TemplateCategory.UTILITY,
        template_status="APPROVED",
        store_has_credentials=True,
        store_credentials_marked_invalid=True,  # the critical flag
        notification_setting_enabled=True,
        has_active_opt_in=False,
        has_opt_out=False,
        window_is_open=True,
        already_sent=False,
    )
    decision = check(ctx)
    assert not decision.allowed
    assert decision.reason == SendSkipReason.CREDENTIALS_INVALID


# ── T071 / TASK-SEC-009 — Meta error whitelist ──────────────────────


def test_sanitize_meta_error_drops_fbtrace_id() -> None:
    raw = {
        "error": {
            "code": 190,
            "error_subcode": 463,
            "message": "Access token has expired",
            "type": "OAuthException",
            "fbtrace_id": "TRACE_DO_NOT_LEAK",
            "error_user_msg": "Verbose user-facing string",
            "error_user_title": "Some Meta UI heading",
        }
    }
    sanitized = sanitize_meta_error(raw)
    assert sanitized == {
        "code": 190,
        "error_subcode": 463,
        "message": "Access token has expired",
        "type": "OAuthException",
    }
    assert "fbtrace_id" not in sanitized
    assert "error_user_msg" not in sanitized


def test_sanitize_meta_error_handles_inner_object_directly() -> None:
    raw = {
        "code": 100,
        "message": "Missing scope",
        "type": "OAuthException",
        "fbtrace_id": "x",
    }
    sanitized = sanitize_meta_error(raw)
    assert sanitized == {
        "code": 100,
        "message": "Missing scope",
        "type": "OAuthException",
    }


def test_sanitize_meta_error_returns_none_on_garbage() -> None:
    assert sanitize_meta_error(None) is None
    assert sanitize_meta_error("not a dict") is None
    assert sanitize_meta_error({"unrelated": "shape"}) == {}


# ── Fixtures expected at conftest level ─────────────────────────────
#   - db_session
#   - seeded_store: a StoreModel under the test tenant
#   - seeded_store_with_byo_credential_error: a StoreModel with
#     settings.whatsapp.credential_error set to a non-empty string
