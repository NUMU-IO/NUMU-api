"""Partner App review rounds, listings and the partner notification feed.

The rules that matter: a partner reads only its own apps' reviews and never
a staff-only note; every submission is a new round with the old ones kept;
the manifest never renames an app, a reviewed listing does; notifications
are scoped to the partner.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers, UploadFile

from src.api.v1.routes import partner_apps as pa
from src.api.v1.routes import partner_portal as portal
from src.api.v1.routes.admin import apps as admin_apps
from src.api.v1.routes.admin import partners as admin_partners
from src.application.services import app_review
from src.application.services.app_review import (
    ListingContent,
    add_business_days,
    business_days_between,
)
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.partner_account import (
    PartnerNotificationModel,
)
from tests.unit.api.test_partner_portal import _ctx, _member, _partner, _user
from tests.unit.test_app_manifest import GOOD


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("src.core.url_guard.assert_webhook_target", lambda url: None)
    sent: list = []

    async def _send(self, message):
        sent.append(message)
        return True

    monkeypatch.setattr(app_review.ResendEmailService, "send_email", _send)
    return sent


async def _app(s, owner, slug="bosta-sync"):
    created = await pa.create_app(
        pa.CreateAppRequest(slug=slug, name_ar="مزامنة بوسطة", name_en="Bosta Sync"),
        user_id=owner.id,
        db=s,
    )
    return created.data.id


async def _upload(s, owner, app_id, version="1.2.0", **changes):
    m = copy.deepcopy(GOOD)
    m["version"] = version
    m.update(changes)
    return (
        await pa.upload_version(
            app_id, pa.UploadVersionRequest(manifest=m), user_id=owner.id, db=s
        )
    ).data


def _listing(**changes) -> ListingContent:
    body = {
        "name": {"ar": "بوسطة برو", "en": "Bosta Pro"},
        "tagline": {"ar": "شحن أسرع", "en": "Faster shipping"},
        "description": {"ar": "وصف", "en": "A description"},
        "screenshots": [{"src": "https://cdn.example.com/s.png"}],
        "video_url": "https://www.youtube.com/watch?v=abc",
        "category": "shipping",
        "keywords": {"ar": ["شحن"], "en": ["shipping", "bosta"]},
    }
    body.update(changes)
    return ListingContent.model_validate(body)


async def _setup(s):
    owner = await _user(s)
    partner = await _partner(s, owner)
    admin = await _user(s)
    app_id = await _app(s, owner)
    return owner, partner, admin, app_id


async def _decide(s, admin, review_id, decision, **kw):
    return await admin_apps.decide(
        review_id,
        admin_apps.ReviewDecision(decision=decision, **kw),
        db=s,
        admin_id=admin.id,
    )


# ─── Review rounds ────────────────────────────────────────────────


async def test_resubmission_opens_a_new_round_and_keeps_history(test_session, _offline):
    s = test_session
    owner, _, admin, app_id = await _setup(s)
    v = await _upload(s, owner, app_id)
    await pa.submit_version(app_id, v.id, user_id=owner.id, db=s)

    queue = (await admin_apps.review_queue(db=s)).data
    assert [r.round for r in queue] == [1]
    assert queue[0].due_at == add_business_days(queue[0].submitted_at, 3)

    detail = (
        await admin_apps.review_detail(queue[0].review_id, db=s, admin_id=admin.id)
    ).data
    assert detail.status == "in_review"
    assert detail.required_checks == list(admin_apps.CHECKLIST)

    await _decide(
        s,
        admin,
        detail.review_id,
        "request_changes",
        notes_ar="عدّل الصلاحيات",
        notes_en="Trim the scopes",
        internal_note="STAFF ONLY: partner is slow",
    )
    await pa.submit_version(app_id, v.id, user_id=owner.id, db=s)

    timeline = (await pa.review_timeline(app_id, user_id=owner.id, db=s)).data
    assert [r.round for r in timeline.rounds] == [2, 1]
    assert timeline.rounds[1].notes == {"ar": "عدّل الصلاحيات", "en": "Trim the scopes"}
    assert timeline.open.position == 1 and timeline.open.queue_length == 1
    assert "STAFF ONLY" not in timeline.model_dump_json()

    history = (
        await admin_apps.review_detail(timeline.open.review_id, db=s, admin_id=admin.id)
    ).data.history
    assert history[1].internal_note == "STAFF ONLY: partner is slow"

    statuses = [
        n.data["status"]
        for n in (await s.execute(PartnerNotificationModel.__table__.select())).all()
    ]
    assert statuses == [
        "submitted",
        "in_review",
        "changes_requested",
        "submitted",
        "in_review",
    ]
    assert len(_offline) == 5


async def test_approve_needs_every_check(test_session):
    s = test_session
    owner, _, admin, app_id = await _setup(s)
    v = await _upload(s, owner, app_id)
    await pa.submit_version(app_id, v.id, user_id=owner.id, db=s)
    review_id = (await admin_apps.review_queue(db=s)).data[0].review_id
    with pytest.raises(HTTPException) as exc:
        await _decide(
            s, admin, review_id, "approve", checklist={"scopes_justified": True}
        )
    assert exc.value.status_code == 422
    await _decide(
        s,
        admin,
        review_id,
        "approve",
        checklist=dict.fromkeys(admin_apps.CHECKLIST, True),
    )
    await pa.publish_version(app_id, v.id, user_id=owner.id, db=s)
    detail = (await pa.get_app(app_id, user_id=owner.id, db=s)).data
    assert detail.status == "published"


async def test_one_open_round_per_app(test_session):
    s = test_session
    owner, _, _, app_id = await _setup(s)
    v = await _upload(s, owner, app_id)
    await pa.save_listing(app_id, _listing(), user_id=owner.id, db=s)
    await pa.submit_version(app_id, v.id, user_id=owner.id, db=s)
    with pytest.raises(HTTPException) as exc:
        await pa.submit_listing(app_id, user_id=owner.id, db=s)
    assert exc.value.status_code == 409


async def test_a_partner_cannot_read_another_partners_app(test_session):
    s = test_session
    _, _, _, app_id = await _setup(s)
    stranger = await _user(s)
    await _partner(s, stranger)
    for call in (pa.review_timeline, pa.get_listing):
        with pytest.raises(HTTPException) as exc:
            await call(app_id, user_id=stranger.id, db=s)
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        await pa.save_listing(app_id, _listing(), user_id=stranger.id, db=s)


# ─── Listing ──────────────────────────────────────────────────────


async def test_the_manifest_never_renames_the_app(test_session):
    s = test_session
    owner, _, _, app_id = await _setup(s)
    m_name = {"ar": "اسم تاني خالص", "en": "Totally Different"}
    out = await pa.upload_version(
        app_id,
        pa.UploadVersionRequest(manifest={**copy.deepcopy(GOOD), "name": m_name}),
        user_id=owner.id,
        db=s,
    )
    assert "listing" in out.message
    app = (await pa.get_app(app_id, user_id=owner.id, db=s)).data
    assert (app.name, app.name_ar) == ("Bosta Sync", "مزامنة بوسطة")


async def test_a_listing_approved_on_its_own_goes_live_and_renames(test_session):
    s = test_session
    owner, _, admin, app_id = await _setup(s)
    await pa.save_listing(app_id, _listing(), user_id=owner.id, db=s)
    preview = (await pa.get_listing(app_id, user_id=owner.id, db=s)).data
    assert preview.preview.listing.video_url == "https://www.youtube.com/watch?v=abc"
    assert preview.draft.status == "draft"

    await pa.submit_listing(app_id, user_id=owner.id, db=s)
    row = (await admin_apps.review_queue(db=s)).data[0]
    assert (row.subject, row.change_type, row.name_change) == (
        "listing",
        "listing_only",
        True,
    )
    detail = (
        await admin_apps.review_detail(row.review_id, db=s, admin_id=admin.id)
    ).data
    assert detail.name_change == {
        "from": {"ar": "مزامنة بوسطة", "en": "Bosta Sync"},
        "to": {"ar": "بوسطة برو", "en": "Bosta Pro"},
    }
    assert detail.required_checks == list(admin_apps.LISTING_CHECKS)

    await _decide(
        s,
        admin,
        row.review_id,
        "approve",
        checklist=dict.fromkeys(admin_apps.LISTING_CHECKS, True),
    )
    app = (await pa.get_app(app_id, user_id=owner.id, db=s)).data
    assert (app.name, app.name_ar) == ("Bosta Pro", "بوسطة برو")
    listing = (await pa.get_listing(app_id, user_id=owner.id, db=s)).data
    assert listing.draft is None
    assert listing.live["keywords"]["en"] == ["shipping", "bosta"]

    # A later version keeps the reviewed listing, not the manifest's copy.
    await _upload(s, owner, app_id, version="1.3.0")
    app = (await pa.get_app(app_id, user_id=owner.id, db=s)).data
    assert app.name == "Bosta Pro"


async def test_a_listing_sent_with_a_version_goes_live_on_publish(test_session):
    s = test_session
    owner, _, admin, app_id = await _setup(s)
    v = await _upload(s, owner, app_id)
    await pa.save_listing(app_id, _listing(), user_id=owner.id, db=s)
    await pa.submit_version(
        app_id,
        v.id,
        user_id=owner.id,
        db=s,
        body=pa.SubmitVersionRequest(with_listing=True),
    )
    row = (await admin_apps.review_queue(db=s)).data[0]
    assert row.subject == "version_listing"
    await _decide(
        s,
        admin,
        row.review_id,
        "approve",
        checklist=dict.fromkeys(admin_apps.CHECKLIST, True),
    )
    assert (await pa.get_app(app_id, user_id=owner.id, db=s)).data.name == "Bosta Sync"
    await pa.publish_version(app_id, v.id, user_id=owner.id, db=s)
    assert (await pa.get_app(app_id, user_id=owner.id, db=s)).data.name == "Bosta Pro"


async def test_the_listing_cannot_be_edited_while_in_review(test_session):
    s = test_session
    owner, _, _, app_id = await _setup(s)
    await pa.save_listing(app_id, _listing(), user_id=owner.id, db=s)
    await pa.submit_listing(app_id, user_id=owner.id, db=s)
    with pytest.raises(HTTPException) as exc:
        await pa.save_listing(app_id, _listing(), user_id=owner.id, db=s)
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"video_url": "https://evil.example.com/v.mp4"},
        {"video_url": "http://www.youtube.com/watch?v=abc"},
        {"keywords": {"ar": [], "en": [f"k{i}" for i in range(11)]}},
        {"category": "not-a-category"},
        {"name": {"ar": "Same", "en": "Same"}},
    ],
)
def test_listing_rules(changes):
    with pytest.raises(ValidationError):
        _listing(**changes)


def test_business_days_skip_the_egyptian_weekend():
    thursday = datetime(2026, 9, 24, 10, tzinfo=UTC)
    assert add_business_days(thursday, 3).date().isoformat() == "2026-09-29"
    assert business_days_between(thursday, datetime(2026, 9, 27, 10, tzinfo=UTC)) == 1


# ─── Notifications ────────────────────────────────────────────────


async def test_notifications_are_scoped_to_the_partner(test_session):
    s = test_session
    owner, partner, admin, _ = await _setup(s)
    dev = await _user(s)
    await _member(s, partner, dev, "developer")
    other = await _user(s)
    await _partner(s, other)
    pending = await _user(s)
    p = await _partner(s, pending)
    p.status = "pending"
    await s.flush()

    out = (
        await admin_partners.post_notice(
            admin_partners.NoticeRequest(
                notice_kind="deprecation",
                title_ar="إيقاف v0",
                title_en="v0 sunset",
                body_ar="هنوقف v0",
                body_en="v0 goes away",
            ),
            db=s,
            admin_id=admin.id,
        )
    ).data
    assert out["recipients"] == 2
    notices = (await admin_partners.list_notices(db=s)).data
    assert [n["recipients"] for n in notices] == [2]

    feed = (await portal.list_notifications(ctx=await _ctx(s, dev), db=s)).data
    assert feed.unread_count == 1
    assert feed.items[0].data["notice_kind"] == "deprecation"

    other_ctx = await _ctx(s, other)
    await portal.mark_notifications_read(
        portal.MarkReadRequest(ids=[feed.items[0].id]), ctx=other_ctx, db=s
    )
    assert (
        await portal.list_notifications(ctx=await _ctx(s, owner), db=s)
    ).data.unread_count == 1

    await portal.mark_notifications_read(
        portal.MarkReadRequest(), ctx=await _ctx(s, dev), db=s
    )
    assert (
        await portal.list_notifications(ctx=await _ctx(s, owner), db=s)
    ).data.unread_count == 0


async def test_status_emails_go_to_the_owner_and_admins_only(test_session, _offline):
    s = test_session
    owner, partner, _, app_id = await _setup(s)
    admin_member, dev_member = await _user(s), await _user(s)
    await _member(s, partner, admin_member, "admin")
    await _member(s, partner, dev_member, "developer")
    v = await _upload(s, owner, app_id)
    await pa.submit_version(app_id, v.id, user_id=owner.id, db=s)
    assert _offline[-1].to == [owner.email, admin_member.email]


async def test_a_suspension_notifies_the_partner(test_session, monkeypatch):
    s = test_session
    owner, _, admin, app_id = await _setup(s)
    await admin_apps.suspend_app(
        app_id,
        admin_apps.Suspension(suspend=True, reason="abuse"),
        db=s,
        admin_id=admin.id,
    )
    feed = (await portal.list_notifications(ctx=await _ctx(s, owner), db=s)).data
    assert feed.items[0].data["status"] == "suspended"
    assert (
        await pa.get_app(app_id, user_id=owner.id, db=s)
    ).data.status == AppStatus.SUSPENDED.value


def test_admin_review_writes_need_the_2fa_step_up():
    route = next(
        r
        for r in admin_apps.router.routes
        if r.path.endswith("/reviews/{review_id}/decision")
    )
    assert any(
        "require_admin_2fa" in d.call.__qualname__ for d in route.dependant.dependencies
    )


async def test_screenshot_upload_is_image_only_and_owner_only(
    test_session, monkeypatch
):
    s = test_session
    owner, _, _, app_id = await _setup(s)
    stranger = await _user(s)
    await _partner(s, stranger)
    stored = []

    class _Storage:
        async def upload_file(self, **kw):
            stored.append(kw["key"])
            return SimpleNamespace(url=f"https://cdn.example.com/{kw['key']}")

    monkeypatch.setattr(pa, "get_storage_service", lambda: _Storage())

    def _file(ctype):
        return UploadFile(
            BytesIO(b"x"), filename="s.png", headers=Headers({"content-type": ctype})
        )

    with pytest.raises(HTTPException) as exc:
        await pa.upload_screenshot(
            app_id, user_id=stranger.id, db=s, file=_file("image/png")
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await pa.upload_screenshot(
            app_id, user_id=owner.id, db=s, file=_file("image/svg+xml")
        )
    assert exc.value.status_code == 400
    out = await pa.upload_screenshot(
        app_id, user_id=owner.id, db=s, file=_file("image/png")
    )
    assert out.data["url"].startswith("https://cdn.example.com/apps/")
    assert len(stored) == 1
