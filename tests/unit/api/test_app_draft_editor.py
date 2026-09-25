"""The partner portal's form editor: section saves land in a lenient draft,
the listing supplies the store texts, and submitting composes both into the
next version under every upload rule."""

from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.v1.routes import partner_apps as pa
from tests.unit.api.test_app_review_listing import (  # noqa: F401
    _app,
    _listing,
    _offline,
    _upload,
)
from tests.unit.api.test_partner_portal import _partner, _user
from tests.unit.test_app_manifest import GOOD

REQUEST = Request({
    "type": "http",
    "method": "GET",
    "path": "/",
    "headers": [],
    "query_string": b"",
    "scheme": "https",
    "server": ("api.numueg.app", 443),
})


async def _setup(s):
    owner = await _user(s)
    await _partner(s, owner)
    return owner, await _app(s, owner)


def _sections() -> dict:
    return {k: copy.deepcopy(v) for k, v in GOOD.items() if k in pa.DRAFT_KEYS}


async def test_a_new_app_starts_with_a_lenient_draft(test_session):
    owner, app_id = await _setup(test_session)
    out = (await pa.get_draft(app_id, REQUEST, user_id=owner.id, db=test_session)).data
    assert out.draft["pricing"] == {"model": "free"}
    assert out.draft["oauth"] == {"redirect_urls": [], "scopes": []}
    assert out.next_version == "1.0.0"
    assert out.problems  # nothing filled in yet
    assert "catalog:read" in out.meta["scopes"]
    assert out.meta["token_url"] == "https://api.numueg.app/api/v1/oauth/token"


async def test_sections_save_one_at_a_time_and_null_removes(test_session):
    owner, app_id = await _setup(test_session)
    await pa.save_draft(
        app_id,
        pa.EditorDraftUpdate(
            changes={"app_url": "https://app.example.com", "embedded": True}
        ),
        REQUEST,
        user_id=owner.id,
        db=test_session,
    )
    out = (
        await pa.save_draft(
            app_id,
            pa.EditorDraftUpdate(changes={"embedded": None}),
            REQUEST,
            user_id=owner.id,
            db=test_session,
        )
    ).data
    assert out.draft["app_url"] == "https://app.example.com"
    assert "embedded" not in out.draft


async def test_only_editor_sections_can_be_saved(test_session):
    owner, app_id = await _setup(test_session)
    with pytest.raises(HTTPException) as exc:
        await pa.save_draft(
            app_id,
            pa.EditorDraftUpdate(changes={"slug": "other", "version": "9.9.9"}),
            REQUEST,
            user_id=owner.id,
            db=test_session,
        )
    assert exc.value.status_code == 422


async def test_another_partner_cannot_read_the_draft(test_session):
    _, app_id = await _setup(test_session)
    stranger = await _user(test_session)
    await _partner(test_session, stranger)
    with pytest.raises(HTTPException) as exc:
        await pa.get_draft(app_id, REQUEST, user_id=stranger.id, db=test_session)
    assert exc.value.status_code == 404


async def test_filled_sections_and_a_listing_submit_as_the_next_version(test_session):
    owner, app_id = await _setup(test_session)
    await pa.save_listing(app_id, _listing(), user_id=owner.id, db=test_session)
    out = (
        await pa.save_draft(
            app_id,
            pa.EditorDraftUpdate(changes=_sections()),
            REQUEST,
            user_id=owner.id,
            db=test_session,
        )
    ).data
    assert out.problems == []

    version = (
        await pa.submit_draft(app_id, REQUEST, user_id=owner.id, db=test_session)
    ).data
    assert version.version == "1.0.0"
    assert version.status == "submitted"
    reviews = (await pa.review_timeline(app_id, user_id=owner.id, db=test_session)).data
    assert reviews.open is not None
    assert reviews.rounds[0].subject == "version_listing"


async def test_an_incomplete_draft_is_refused_with_every_problem(test_session):
    owner, app_id = await _setup(test_session)
    with pytest.raises(HTTPException) as exc:
        await pa.submit_draft(app_id, REQUEST, user_id=owner.id, db=test_session)
    assert exc.value.status_code == 422
    assert "\n" in exc.value.detail


async def test_a_cli_upload_reseeds_the_draft(test_session):
    owner, app_id = await _setup(test_session)
    await pa.save_draft(
        app_id,
        pa.EditorDraftUpdate(changes={"app_url": "https://stale.example.com"}),
        REQUEST,
        user_id=owner.id,
        db=test_session,
    )
    await _upload(test_session, owner, app_id, version="1.2.0")
    out = (await pa.get_draft(app_id, REQUEST, user_id=owner.id, db=test_session)).data
    assert out.draft["app_url"] == GOOD["app_url"]
    assert out.next_version == "1.2.1"


async def test_a_draft_app_can_be_deleted(test_session):
    owner, app_id = await _setup(test_session)
    await pa.delete_app(app_id, user_id=owner.id, db=test_session)
    with pytest.raises(HTTPException) as exc:
        await pa.get_app(app_id, user_id=owner.id, db=test_session)
    assert exc.value.status_code == 404


async def test_a_live_app_cannot_be_deleted(test_session):
    from src.core.entities.app import AppStatus
    from src.infrastructure.database.models.public.app import AppModel

    owner, app_id = await _setup(test_session)
    (await test_session.get(AppModel, app_id)).status = AppStatus.PUBLISHED
    with pytest.raises(HTTPException) as exc:
        await pa.delete_app(app_id, user_id=owner.id, db=test_session)
    assert exc.value.status_code == 409


async def test_create_takes_the_category_and_tags_into_the_listing(test_session):
    owner = await _user(test_session)
    await _partner(test_session, owner)
    created = (
        await pa.create_app(
            pa.CreateAppRequest(
                slug="smart-stock",
                name_ar="المخزون الذكي",
                name_en="Smart Stock",
                category="inventory",
                tags=["stock", " alerts "],
            ),
            user_id=owner.id,
            db=test_session,
        )
    ).data
    assert created.created_at is not None
    listing = (await pa.get_listing(created.id, user_id=owner.id, db=test_session)).data
    assert listing.live["category"] == "inventory"
    assert listing.live["keywords"]["en"] == ["stock", "alerts"]


async def test_an_unknown_category_is_refused(test_session):
    owner = await _user(test_session)
    await _partner(test_session, owner)
    with pytest.raises(HTTPException) as exc:
        await pa.create_app(
            pa.CreateAppRequest(
                slug="x-app", name_ar="تطبيق", name_en="X App", category="nope"
            ),
            user_id=owner.id,
            db=test_session,
        )
    assert exc.value.status_code == 422


async def test_a_partial_listing_saves_but_does_not_submit(test_session):
    owner, app_id = await _setup(test_session)
    partial = pa.ListingDraftContent.model_validate({
        "name": {"ar": "اشترك", "en": "Eshtarek"},
        "category": "sales",
    })
    await pa.save_listing(app_id, partial, user_id=owner.id, db=test_session)
    listing = (await pa.get_listing(app_id, user_id=owner.id, db=test_session)).data
    assert listing.draft.content["tagline"] == {"ar": "", "en": ""}
    with pytest.raises(HTTPException) as exc:
        await pa.submit_listing(app_id, user_id=owner.id, db=test_session)
    assert exc.value.status_code == 422
    assert "listing.tagline" in exc.value.detail


async def test_a_draft_apps_icon_shows_as_soon_as_it_is_saved(test_session):
    owner, app_id = await _setup(test_session)
    await pa.save_draft(
        app_id,
        pa.EditorDraftUpdate(changes={"icon": "https://cdn.example.com/icon.png"}),
        REQUEST,
        user_id=owner.id,
        db=test_session,
    )
    app = (await pa.get_app(app_id, user_id=owner.id, db=test_session)).data
    assert app.icon_url == "https://cdn.example.com/icon.png"
