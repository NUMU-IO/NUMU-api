"""Inbox routes refuse another store's thread or channel connection by id.

The handlers loaded threads and connections by id alone, so a merchant could
read, send into, or mark another store's customer conversations by putting
that store's thread id under their own `/stores/{store_id}` path.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db
from src.api.dependencies.auth import get_current_store
from src.api.dependencies.repositories import (
    get_channel_connection_repository,
    get_channel_message_repository,
    get_message_thread_repository,
    get_whatsapp_template_repository,
)
from src.main import app

STORE_A = uuid4()
STORE_B = uuid4()
THREAD_B = uuid4()
CONNECTION_B = uuid4()


class _Threads:
    async def get_by_id(self, thread_id):
        if thread_id == THREAD_B:
            return SimpleNamespace(id=THREAD_B, store_id=STORE_B)
        return None


class _Connections:
    async def get_by_id(self, connection_id):
        if connection_id == CONNECTION_B:
            return SimpleNamespace(id=CONNECTION_B, store_id=STORE_B)
        return None


class _Untouched:
    """Any read through here means the guard let a foreign id through."""

    def __getattr__(self, name):
        raise AssertionError(f"foreign id reached the repository: {name}")


async def _no_db():
    yield None


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_current_store] = lambda: SimpleNamespace(id=STORE_A)
    app.dependency_overrides[get_message_thread_repository] = lambda: _Threads()
    app.dependency_overrides[get_channel_connection_repository] = lambda: _Connections()
    app.dependency_overrides[get_channel_message_repository] = lambda: _Untouched()
    app.dependency_overrides[get_whatsapp_template_repository] = lambda: _Untouched()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", f"threads/{THREAD_B}"),
        ("post", f"threads/{THREAD_B}/read"),
        ("post", f"threads/{THREAD_B}/resolve"),
        ("get", f"threads/{THREAD_B}/messages/"),
        ("get", f"whatsapp/?connection_id={CONNECTION_B}"),
    ],
)
def test_foreign_thread_or_connection_is_not_found(client, method, path):
    response = getattr(client, method)(f"/api/v1/stores/{STORE_A}/{path}")

    assert response.status_code == 404, response.text[:200]


def test_foreign_thread_send_is_not_found(client):
    response = client.post(
        f"/api/v1/stores/{STORE_A}/threads/{THREAD_B}/messages/send",
        json={"text": "hi"},
    )

    assert response.status_code == 404, response.text[:200]


def test_foreign_connection_template_create_is_not_found(client):
    response = client.post(
        f"/api/v1/stores/{STORE_A}/whatsapp/",
        json={
            "connection_id": str(CONNECTION_B),
            "name": "promo",
            "category": "MARKETING",
            "language": "en",
        },
    )

    assert response.status_code == 404, response.text[:200]
