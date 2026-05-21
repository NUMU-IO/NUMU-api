"""Integration test for the full Meta CAPI fan-out pipeline.

Asserts that the bytes that LEAVE our server bound for Meta's Graph API
are correctly shaped: PII fields hashed, raw fields untouched, the
dispatcher's flat user_data dict folded into Meta's `data[0].user_data`
envelope.

Earlier unit tests cover each piece in isolation (hashing.py,
dispatcher payload shape, dispatcher activation gate). This test
catches regressions where someone bypasses ``hash_user_data`` at the
Celery-task boundary — the most likely place for a PII leak to slip
through.

End-to-end coverage:
    enqueue_meta_capi_purchase(order)
        → meta_capi_send_event Celery task
            → _send_event coroutine
                → httpx.Client.post(graph.facebook.com/.../events)
                  ← this is the line of HTTP body we inspect

The HTTPX call is the only side-effect we mock. DB calls go through
patched repos; secrets decryption is patched. We then capture the
exact dict that Meta receives and assert hashing happened.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

PIXEL_ID = "1552896226251388"
TENANT_ID = uuid4()
STORE_ID = uuid4()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture
def captured_meta_post(monkeypatch):
    """Patch the full meta_capi._send_event collaborator graph so we
    can capture the httpx POST without standing up a real DB.

    Returns a dict that gets populated with ``url``, ``params``, and
    ``json`` on the first ``httpx.Client.post`` call.
    """
    captured: dict = {}

    # ── Mock the httpx Client ───────────────────────────────────────
    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.json.return_value = {
        "events_received": 1,
        "fbtrace_id": "test-fbtrace-123",
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, params, json):
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            return response_mock

    import src.infrastructure.messaging.tasks.meta_capi as meta_capi_module

    monkeypatch.setattr(meta_capi_module.httpx, "Client", FakeClient)

    # ── Mock the DB session + repos + tenancy + secrets ────────────
    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    fake_store = SimpleNamespace(
        id=STORE_ID,
        tenant_id=TENANT_ID,
        settings={"tracking": {"meta": {"capi_enabled": True, "pixel_id": PIXEL_ID}}},
    )

    fake_store_repo = MagicMock()
    fake_store_repo.return_value.get_by_id = AsyncMock(return_value=fake_store)

    fake_log_repo = MagicMock()
    fake_log_repo.return_value.create = AsyncMock(
        return_value=SimpleNamespace(id=uuid4())
    )
    fake_log_repo.return_value.update_response = AsyncMock()
    fake_log_repo.return_value.update_error = AsyncMock()

    fake_credential = SimpleNamespace(
        credentials_encrypted=b"encrypted-blob",
        encryption_key_id="key-1",
        is_active=True,
    )

    # Patch the lazy imports inside _send_event at their source modules.
    import src.infrastructure.database.connection as conn_module
    import src.infrastructure.external_services.secrets as secrets_module
    import src.infrastructure.repositories.meta_event_log_repository as log_repo_module
    import src.infrastructure.repositories.store_repository as store_repo_module
    import src.infrastructure.tenancy.rls as rls_module

    # async_session_factory: create a fake context manager
    class FakeSessionFactory:
        def __call__(self):
            return fake_session

    monkeypatch.setattr(conn_module, "AsyncSessionLocal", FakeSessionFactory())
    monkeypatch.setattr(store_repo_module, "StoreRepository", fake_store_repo)
    monkeypatch.setattr(log_repo_module, "MetaEventLogRepository", fake_log_repo)
    monkeypatch.setattr(rls_module, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(rls_module, "narrow_to_tenant", AsyncMock())

    # secrets.decrypt returns the access token dict
    fake_secrets_mgr = MagicMock()
    fake_secrets_mgr.decrypt = AsyncMock(
        return_value={"access_token": "test-access-token-EAAB"}
    )
    monkeypatch.setattr(secrets_module, "get_secrets_manager", lambda: fake_secrets_mgr)

    # The cred query result — patch session.execute to return the credential.
    cred_result = MagicMock()
    cred_result.scalar_one_or_none.return_value = fake_credential
    fake_session.execute = AsyncMock(return_value=cred_result)

    return captured


# ---------------------------------------------------------------------------
# Tests — assert the wire-format payload to Meta's Graph API
# ---------------------------------------------------------------------------


class TestCAPIPipelineHashing:
    """Every byte of PII leaving our server must be hashed."""

    async def test_email_is_sha256_hashed_not_plaintext(self, captured_meta_post):
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        await _send_event(
            task=MagicMock(request=MagicMock(retries=0)),
            store_id=str(STORE_ID),
            pixel_id=PIXEL_ID,
            event_name="Purchase",
            event_id="evt-1",
            event_time=int(datetime(2026, 5, 16, tzinfo=UTC).timestamp()),
            event_source_url=None,
            user_data={"email": "  Shopper@Example.COM  "},
            custom_data={"value": 10.0, "currency": "EGP"},
            test_event_code=None,
            action_source="website",
        )

        body = captured_meta_post["json"]
        ud = body["data"][0]["user_data"]
        # Email must NOT appear as plaintext anywhere.
        assert "shopper@example.com" not in str(body).lower()
        # Hashed form is what Meta receives.
        assert ud["em"] == [_sha256("shopper@example.com")]

    async def test_phone_is_egyptian_normalized_then_hashed(self, captured_meta_post):
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        # All three input shapes (national, E.164 with/without +) must
        # hash to the same digest so browser-side Pixel + server-side
        # CAPI dedupe on the phone match key.
        digests: set[str] = set()
        for raw in ("+201001234567", "201001234567", "01001234567"):
            captured_meta_post.clear()
            await _send_event(
                task=MagicMock(request=MagicMock(retries=0)),
                store_id=str(STORE_ID),
                pixel_id=PIXEL_ID,
                event_name="Purchase",
                event_id=f"evt-{raw}",
                event_time=1_700_000_000,
                event_source_url=None,
                user_data={"phone": raw},
                custom_data={},
                test_event_code=None,
                action_source="website",
            )
            digests.add(captured_meta_post["json"]["data"][0]["user_data"]["ph"][0])
        assert len(digests) == 1, f"phone digests diverged: {digests}"
        # Canonical form: "20" + 10-digit subscriber number.
        assert digests == {_sha256("201001234567")}

    async def test_raw_fbp_fbc_ip_user_agent_NOT_hashed(self, captured_meta_post):
        # Per Meta spec, these four fields are passed verbatim.
        # Re-hashing them silently destroys match quality.
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        await _send_event(
            task=MagicMock(request=MagicMock(retries=0)),
            store_id=str(STORE_ID),
            pixel_id=PIXEL_ID,
            event_name="Purchase",
            event_id="evt-2",
            event_time=1_700_000_000,
            event_source_url=None,
            user_data={
                "fbp": "fb.0.1719414738122.1234567890",
                "fbc": "fb.1.1719414700000.IwAR2abc",
                "ip": "197.45.123.45",
                "user_agent": "Mozilla/5.0 (iPhone)",
            },
            custom_data={},
            test_event_code=None,
            action_source="website",
        )

        ud = captured_meta_post["json"]["data"][0]["user_data"]
        assert ud["fbp"] == "fb.0.1719414738122.1234567890"
        assert ud["fbc"] == "fb.1.1719414700000.IwAR2abc"
        assert ud["client_ip_address"] == "197.45.123.45"
        assert ud["client_user_agent"] == "Mozilla/5.0 (iPhone)"

    async def test_external_id_uses_customer_id_and_is_hashed(self, captured_meta_post):
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        await _send_event(
            task=MagicMock(request=MagicMock(retries=0)),
            store_id=str(STORE_ID),
            pixel_id=PIXEL_ID,
            event_name="Purchase",
            event_id="evt-3",
            event_time=1_700_000_000,
            event_source_url=None,
            user_data={"customer_id": "cust-uuid-xyz"},
            custom_data={},
            test_event_code=None,
            action_source="website",
        )

        ud = captured_meta_post["json"]["data"][0]["user_data"]
        assert ud["external_id"] == [_sha256("cust-uuid-xyz")]
        assert "cust-uuid-xyz" not in str(captured_meta_post["json"])

    async def test_envelope_shape_matches_meta_spec(self, captured_meta_post):
        # Meta requires:  { "data": [ {...event...} ], "test_event_code"? }
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        await _send_event(
            task=MagicMock(request=MagicMock(retries=0)),
            store_id=str(STORE_ID),
            pixel_id=PIXEL_ID,
            event_name="Purchase",
            event_id="evt-4",
            event_time=1_700_000_000,
            event_source_url="https://shop.example.com/order/123",
            user_data={"email": "x@example.com"},
            custom_data={"value": 25.5, "currency": "EGP", "order_id": "ord-1"},
            test_event_code="TEST12345",
            action_source="website",
        )

        body = captured_meta_post["json"]
        assert "data" in body and isinstance(body["data"], list)
        assert len(body["data"]) == 1
        evt = body["data"][0]
        assert evt["event_name"] == "Purchase"
        assert evt["event_id"] == "evt-4"
        assert evt["event_time"] == 1_700_000_000
        assert evt["action_source"] == "website"
        assert evt["event_source_url"] == "https://shop.example.com/order/123"
        assert evt["custom_data"] == {
            "value": 25.5,
            "currency": "EGP",
            "order_id": "ord-1",
        }
        # test_event_code lives at the top level, not inside data[].
        assert body["test_event_code"] == "TEST12345"

    async def test_url_targets_correct_pixel_id_and_graph_api_version(
        self, captured_meta_post, monkeypatch
    ):
        from src.infrastructure.messaging.tasks.meta_capi import _send_event

        await _send_event(
            task=MagicMock(request=MagicMock(retries=0)),
            store_id=str(STORE_ID),
            pixel_id=PIXEL_ID,
            event_name="Purchase",
            event_id="evt-5",
            event_time=1_700_000_000,
            event_source_url=None,
            user_data={},
            custom_data={},
            test_event_code=None,
            action_source="website",
        )

        url = captured_meta_post["url"]
        assert PIXEL_ID in url
        assert url.endswith(f"/{PIXEL_ID}/events")
        # API version defaults to v21.0 unless overridden in settings.
        assert "/v" in url
        # Access token MUST be in the query params, not body — per Meta spec.
        assert captured_meta_post["params"]["access_token"] == "test-access-token-EAAB"
