"""Product requests — the storefront form and the merchant's inbox.

Calls the route handlers directly with fakes, so no Postgres is required.

Two things here are easy to get wrong and expensive to get wrong:

* the honeypot and the rate limit must answer like a success and write
  nothing — a bot that can tell a rejection from an acceptance learns how to
  get past the check;
* `handled_at` records how long a request sat unanswered, so it is stamped the
  first time the request leaves "new" and never reset by a later status change.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.storefront import product_requests as storefront_requests
from src.api.v1.routes.stores.product_requests import (
    UpdateProductRequest,
    update_product_request,
)


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, row):
        row.id = uuid4()
        row.created_at = None
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None

    async def scalar(self, _query):
        return None


class _FakeStorage:
    def __init__(self):
        self.uploads = 0

    async def upload_file(self, **kwargs):
        self.uploads += 1
        return SimpleNamespace(url=f"https://cdn.test/{self.uploads}.jpg")


class _FakeEmail:
    def __init__(self):
        self.sent = []

    async def send_email(self, message):
        self.sent.append(message)


class _FakeStoreRepo:
    def __init__(self, store):
        self._store = store

    async def get_by_id(self, _store_id):
        return self._store


def _store():
    return SimpleNamespace(
        id=uuid4(), name="Pixel Print", contact_email="shop@example.com"
    )


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.7"))


async def _submit(db, *, website=None, store=None, email=None, monkeypatch=None):
    store = store or _store()
    return await storefront_requests.create_product_request(
        request=_request(),
        store_id=store.id,
        db=db,
        store_repo=_FakeStoreRepo(store),
        storage=_FakeStorage(),
        email_service=email or _FakeEmail(),
        name="Yahia",
        email="reader@example.com",
        details="Looking for ISBN 9780316556347, hardcover",
        phone="+201000000000",
        locale="en",
        source_url="https://pixelprint.numueg.app/",
        website=website,
        images=None,
    )


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Redis is not available in tests; the limiter is exercised separately."""

    async def _allow(_store_id, _ip):
        return False

    monkeypatch.setattr(storefront_requests, "_over_rate_limit", _allow)


@pytest.mark.asyncio
async def test_request_is_saved_and_the_merchant_is_emailed():
    db, email = _FakeSession(), _FakeEmail()
    resp = await _submit(db, email=email)

    assert len(db.added) == 1
    row = db.added[0]
    assert row.status == "new"
    assert row.email == "reader@example.com"
    assert resp.data.received is True
    assert len(email.sent) == 1
    assert "Yahia" in email.sent[0].subject


@pytest.mark.asyncio
async def test_honeypot_writes_nothing_but_looks_like_success():
    db, email = _FakeSession(), _FakeEmail()
    resp = await _submit(db, website="http://spam.example", email=email)

    assert db.added == []
    assert email.sent == []
    assert resp.data.received is True
    assert resp.data.id is None


@pytest.mark.asyncio
async def test_rate_limited_submission_also_looks_like_success(monkeypatch):
    async def _deny(_store_id, _ip):
        return True

    monkeypatch.setattr(storefront_requests, "_over_rate_limit", _deny)
    db = _FakeSession()
    resp = await _submit(db)

    assert db.added == []
    assert resp.data.received is True


@pytest.mark.asyncio
async def test_email_failure_does_not_lose_the_request():
    class _BrokenEmail:
        async def send_email(self, _message):
            raise RuntimeError("smtp down")

    db = _FakeSession()
    resp = await _submit(db, email=_BrokenEmail())

    assert len(db.added) == 1
    assert resp.data.id is not None


class _RowSession(_FakeSession):
    def __init__(self, row):
        super().__init__()
        self.row = row

    async def scalar(self, _query):
        return self.row


def _row(status="new"):
    return SimpleNamespace(
        id=uuid4(),
        store_id=uuid4(),
        name="Yahia",
        email="reader@example.com",
        phone=None,
        details="ISBN 9780316556347",
        images=[],
        status=status,
        note=None,
        source_url=None,
        locale="en",
        handled_at=None,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_status_change_stamps_handled_at_once():
    row = _row()
    db = _RowSession(row)
    store = SimpleNamespace(id=row.store_id)

    await update_product_request(
        request_id=row.id,
        payload=UpdateProductRequest(status="contacted"),
        store=store,
        db=db,
    )
    first = row.handled_at
    assert first is not None

    await update_product_request(
        request_id=row.id,
        payload=UpdateProductRequest(status="sourced"),
        store=store,
        db=db,
    )
    assert row.status == "sourced"
    assert row.handled_at == first  # not reset by the second move


@pytest.mark.asyncio
async def test_unknown_status_is_refused():
    row = _row()
    db = _RowSession(row)
    with pytest.raises(HTTPException) as caught:
        await update_product_request(
            request_id=row.id,
            payload=UpdateProductRequest(status="banana"),
            store=SimpleNamespace(id=row.store_id),
            db=db,
        )
    assert caught.value.status_code == 400
    assert row.status == "new"
