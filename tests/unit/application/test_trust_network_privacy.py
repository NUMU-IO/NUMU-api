"""Unit tests for GDPR propagation to the standalone Trust Network (C1).

Same conventions as test_trust_network_feed.py: env via monkeypatch,
network via httpx.MockTransport — no real TN needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.application.services.trust_network_privacy import (
    fetch_export,
    post_erasure,
    privacy_config,
)

TOKEN = "a" * 64


@pytest.fixture
def tn_env(monkeypatch):
    monkeypatch.setenv("TRUST_NETWORK_URL", "http://trust-network:8000")
    monkeypatch.setenv("TRUST_NETWORK_API_KEY", "sk_test_demo")
    monkeypatch.setenv("TRUST_NETWORK_TIMEOUT_SECONDS", "2.0")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_config_disabled_without_url_or_key(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_URL", raising=False)
    monkeypatch.delenv("TRUST_NETWORK_API_KEY", raising=False)
    assert privacy_config()["enabled"] is False


@pytest.mark.asyncio
async def test_erasure_skips_when_not_configured(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_URL", raising=False)
    monkeypatch.delenv("TRUST_NETWORK_API_KEY", raising=False)
    done, detail = await post_erasure(TOKEN)
    assert done is True
    assert detail == "not_configured"


@pytest.mark.asyncio
async def test_erasure_success(tn_env):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"token_prefix": TOKEN[:8], "erased": {"decisions": 2}},
        )

    async with _client(handler) as client:
        done, detail = await post_erasure(TOKEN, client=client)

    assert done is True
    assert detail == "erased"
    assert seen["url"].endswith("/v1/data-subjects/erasure")
    assert seen["auth"] == "Bearer sk_test_demo"
    assert seen["body"] == {"token": TOKEN, "cluster_wide": False}


@pytest.mark.asyncio
async def test_erasure_5xx_requests_retry(tn_env):
    async with _client(lambda r: httpx.Response(500, text="boom")) as client:
        done, detail = await post_erasure(TOKEN, client=client)
    assert done is False
    assert detail == "http_500"


@pytest.mark.asyncio
async def test_erasure_403_scope_misconfig_requests_retry(tn_env):
    async with _client(
        lambda r: httpx.Response(403, text="missing scope data_subjects:write")
    ) as client:
        done, detail = await post_erasure(TOKEN, client=client)
    assert done is False
    assert detail == "denied_403"


@pytest.mark.asyncio
async def test_erasure_transport_error_requests_retry(tn_env):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(handler) as client:
        done, detail = await post_erasure(TOKEN, client=client)
    assert done is False
    assert detail.startswith("transport_error")


@pytest.mark.asyncio
async def test_export_success_and_failure(tn_env):
    export_body = {"token_prefix": TOKEN[:8], "data": {"reputation": {"score": 55}}}

    async with _client(lambda r: httpx.Response(200, json=export_body)) as client:
        assert await fetch_export(TOKEN, client=client) == export_body

    async with _client(lambda r: httpx.Response(503, text="down")) as client:
        assert await fetch_export(TOKEN, client=client) is None


@pytest.mark.asyncio
async def test_export_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("TRUST_NETWORK_URL", raising=False)
    monkeypatch.delenv("TRUST_NETWORK_API_KEY", raising=False)
    assert await fetch_export(TOKEN) is None


def test_erasure_task_registered_and_imported():
    """Dead-beat-task guard: name registered AND module in celery imports."""
    import src.infrastructure.messaging.tasks.trust_network_privacy_tasks  # noqa: F401
    from src.infrastructure.messaging.celery_app import celery_app

    assert "tasks.trust_network.erase_subject" in celery_app.tasks
    assert (
        "src.infrastructure.messaging.tasks.trust_network_privacy_tasks"
        in celery_app.conf.imports
    )


def test_redact_handler_queues_tn_erasure_structurally():
    """The customers/redact branch must enqueue the TN erasure task."""
    import inspect

    import src.api.v1.routes.shopify.webhooks as wh

    source = inspect.getsource(wh)
    redact_branch = source.split('elif topic == "customers/redact":')[1].split(
        'elif topic == "customers/data_request":'
    )[0]
    assert "erase_subject_from_trust_network" in redact_branch
    assert ".delay(" in redact_branch

    dr_branch = source.split('elif topic == "customers/data_request":')[1]
    assert "fetch_export" in dr_branch
