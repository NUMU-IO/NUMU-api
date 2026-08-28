"""``/track`` → TikTok Events API: ``body.user_data`` is untrusted input.

Sibling of ``test_track_user_data_allowlist.py``, which covers the Meta half.
The allowlist is a security control on both rails for the same reason: BYOT
theme bundles execute on the storefront's own origin, so any script a merchant
installs can POST this dict.

The TikTok branch was merged verbatim (``dict(body.user_data or {})``) and
every server-derived signal only filled a blank, so a page script could set
``ip`` / ``user_agent`` / ``external_id`` / ``ttclid`` and replace exactly the
match keys that carry anonymous traffic. A real value that is uniformly wrong
is worse than no value: it does not merely fail to match, it mis-clusters
distinct visitors into one identity while every diagnostic still reports the
parameter as covered.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.api.v1.routes.storefront.tracking import _maybe_enqueue_tiktok_capi

PIXEL_ID = "D9GH5NRC77U5KEVKREF0"
REAL_IP = "197.54.10.20"
REAL_UA = "Mozilla/5.0 (iPhone)"
FINGERPRINT = "01J8ZQ4M0GDT4W2CJH8N6Y7X5R"


@pytest.fixture
def sent(monkeypatch):
    """Capture the payload handed to the Celery task."""
    calls: list[dict] = []

    class _Task:
        @staticmethod
        def delay(**kwargs):
            calls.append(kwargs)

    import src.infrastructure.messaging.tasks.tiktok_capi as tt

    monkeypatch.setattr(tt, "tiktok_capi_send_event", _Task, raising=False)
    return calls


def _store():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        settings={
            "tracking": {
                "tiktok": {
                    "pixel_id": PIXEL_ID,
                    "pixel_enabled": True,
                    "api_enabled": True,
                }
            }
        },
    )


def _body(user_data=None, **kw):
    base = {
        "event_id": "evt-1",
        "event_time": None,
        "page_url": "https://vionneeg.com/products/scarf",
        "user_data": user_data,
        "ttclid": None,
        "ttp": None,
        "fingerprint": FINGERPRINT,
        "customer_id": None,
        "step_data": {},
        "opt_out": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


async def _run(body, store=None, sent_ip=REAL_IP, sent_ua=REAL_UA):
    await _maybe_enqueue_tiktok_capi(
        store=store or _store(),
        step="product_view",
        body=body,
        ip=sent_ip,
        user_agent=sent_ua,
        session=None,
        landing_ttclid=None,
    )


class TestServerSignalsWin:
    @pytest.mark.asyncio
    async def test_payload_cannot_override_the_request_ip_or_user_agent(self, sent):
        await _run(
            _body({"ip": "1.2.3.4", "user_agent": "EvilBot/1.0", "email": "a@b.com"})
        )
        ud = sent[0]["user_data"]
        assert ud["ip"] == REAL_IP
        assert ud["user_agent"] == REAL_UA

    @pytest.mark.asyncio
    async def test_payload_cannot_forge_external_id(self, sent):
        """`external_id` is TikTok's identity-clustering key — a script that
        pins it to a constant merges every shopper into one person."""
        await _run(_body({"external_id": "ATTACKER_CONSTANT"}))
        assert sent[0]["user_data"]["external_id"] == FINGERPRINT

    @pytest.mark.asyncio
    async def test_payload_cannot_inject_a_click_id(self, sent):
        """`ttclid` decides which ad TikTok credits. It comes from the cookie
        or the attribution envelope, never from the page."""
        await _run(_body({"ttclid": "FORGED_CLICK"}))
        assert "ttclid" not in sent[0]["user_data"]

    @pytest.mark.asyncio
    async def test_genuine_click_id_from_the_landing_is_kept(self, sent):
        await _maybe_enqueue_tiktok_capi(
            store=_store(),
            step="product_view",
            body=_body(),
            ip=REAL_IP,
            user_agent=REAL_UA,
            session=None,
            landing_ttclid="REAL_TT_CLICK",
        )
        assert sent[0]["user_data"]["ttclid"] == "REAL_TT_CLICK"


class TestAllowlistedPii:
    @pytest.mark.asyncio
    async def test_the_eight_pii_fields_still_pass_through(self, sent):
        await _run(
            _body({
                "email": "sara@example.com",
                "phone": "+201234567890",
                "first_name": "Sara",
                "last_name": "Ali",
                "city": "Cairo",
                "state": "Cairo",
                "zip": "11511",
                "country_code": "eg",
            })
        )
        ud = sent[0]["user_data"]
        assert ud["email"] == "sara@example.com"
        assert ud["phone"] == "+201234567890"
        assert ud["country_code"] == "eg"

    @pytest.mark.asyncio
    async def test_unknown_keys_are_dropped_silently(self, sent):
        await _run(_body({"email": "sara@example.com", "totally_made_up": "x"}))
        assert "totally_made_up" not in sent[0]["user_data"]

    @pytest.mark.asyncio
    async def test_no_user_data_at_all_is_fine(self, sent):
        await _run(_body(None))
        ud = sent[0]["user_data"]
        assert ud["ip"] == REAL_IP
        assert ud["external_id"] == FINGERPRINT


class TestGating:
    @pytest.mark.asyncio
    async def test_api_disabled_store_enqueues_nothing(self, sent):
        store = _store()
        store.settings["tracking"]["tiktok"]["api_enabled"] = False
        await _run(_body(), store=store)
        assert sent == []
