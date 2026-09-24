"""App reviews and support threads: who may write, who may read."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from src.api.dependencies.partners import partner_context
from src.api.v1.routes import app_reviews as reviews
from src.api.v1.routes import app_support as support
from src.core.entities.app import AppStatus
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppUninstallEventModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
    PartnerMemberModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    async def allow(*_a, **_k):
        return True, 0, 0

    monkeypatch.setattr(support, "_check_per_user_limit", allow)


class _Mail:
    def __init__(self):
        self.sent = []

    async def send_email(self, message):
        self.sent.append(message)
        return True


class _Storage:
    async def upload_file(self, *, file_content, filename, content_type, bucket):
        return SimpleNamespace(url=f"https://cdn.example/{filename}")


async def _user(s):
    u = UserModel(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="T",
        last_name="U",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    s.add(u)
    await s.flush()
    return u


async def _store(s, name="Shop"):
    owner = await _user(s)
    sub = f"s{uuid4().hex[:8]}"
    tenant = TenantModel(
        id=uuid4(),
        name=name,
        subdomain=sub,
        plan="pro",
        lifecycle_state="active",
        owner_id=owner.id,
    )
    s.add(tenant)
    await s.flush()
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=owner.id,
        name=name,
        slug=sub,
        subdomain=sub,
        settings={},
    )
    s.add(store)
    await s.flush()
    return SimpleNamespace(
        id=store.id, name=name, owner_id=owner.id, tenant_id=tenant.id
    )


async def _partner(s):
    owner = await _user(s)
    account = PartnerAccountModel(
        id=uuid4(),
        user_id=owner.id,
        kind="company",
        display_name="Acme",
        country="EG",
        support_email=f"support-{uuid4().hex[:6]}@acme.test",
        status="approved",
        agreement_version="2026-09-draft",
    )
    s.add(account)
    await s.flush()
    return owner, account


async def _app(s, developer_id=None):
    app = AppModel(
        id=uuid4(),
        slug=f"a-{uuid4().hex[:8]}",
        name="Acme App",
        developer_id=developer_id,
        status=AppStatus.PUBLISHED,
        manifest={},
        listing_flags={},
    )
    s.add(app)
    await s.flush()
    return app


async def _install(s, app, store, days_ago=10):
    inst = AppInstallationModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        app_id=app.id,
        is_enabled=True,
        settings={},
        status="active",
        granted_scopes=[],
    )
    s.add(inst)
    await s.flush()
    inst.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    await s.flush()
    return inst


async def _ctx(s, user):
    return await partner_context(user=(user.id, "store_owner"), db=s)


async def _review(s, app, store, rating=4, mail=None):
    return (
        await reviews.merchant_upsert(
            app.slug,
            reviews.ReviewIn(rating=rating, body="Solid"),
            store=store,
            user_id=store.owner_id,
            db=s,
            email_service=mail or _Mail(),
        )
    ).data


def _file(name, content):
    return UploadFile(filename=name, file=io.BytesIO(content))


# ─── Reviews ──────────────────────────────────────────────────────


async def test_reviewing_needs_seven_days_installed(test_session):
    owner, _ = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store = await _store(test_session)
    await _install(test_session, app, store, days_ago=2)
    with pytest.raises(HTTPException) as exc:
        await _review(test_session, app, store)
    assert exc.value.status_code == 403

    stranger = await _store(test_session)
    with pytest.raises(HTTPException):
        await _review(test_session, app, stranger)


async def test_one_review_per_store_edits_in_place_and_notifies_partner_once(
    test_session,
):
    owner, account = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store = await _store(test_session)
    await _install(test_session, app, store)
    mail = _Mail()
    first = await _review(test_session, app, store, rating=2, mail=mail)
    second = await _review(test_session, app, store, rating=5, mail=mail)
    assert first.id == second.id and second.rating == 5
    assert [m.to for m in mail.sent] == [account.support_email]

    page = (await reviews.merchant_list(app.slug, store=store, db=test_session)).data
    assert page.summary.count == 1 and page.summary.average == 5
    assert page.mine.id == first.id and page.can_review

    await reviews.merchant_delete(app.slug, store=store, db=test_session)
    assert (await reviews.rating_summaries(test_session, [app.id])) == {}


async def test_a_past_install_of_seven_days_qualifies(test_session):
    app = await _app(test_session)
    store, brief = await _store(test_session), await _store(test_session)
    now = datetime.now(UTC)
    test_session.add_all([
        AppUninstallEventModel(
            app_id=app.id, store_id=store.id, installed_at=now - timedelta(days=9)
        ),
        AppUninstallEventModel(
            app_id=app.id, store_id=brief.id, installed_at=now - timedelta(days=1)
        ),
    ])
    await test_session.flush()
    assert await reviews.can_review(test_session, app.id, store.id)
    assert not await reviews.can_review(test_session, app.id, brief.id)


async def test_partner_replies_only_to_its_own_apps(test_session):
    owner, _ = await _partner(test_session)
    other, _ = await _partner(test_session)
    app = await _app(test_session, owner.id)
    theirs = await _app(test_session, other.id)
    store = await _store(test_session)
    await _install(test_session, app, store)
    await _install(test_session, theirs, store)
    mine = await _review(test_session, app, store)
    foreign = await _review(test_session, theirs, store)

    out = (
        await reviews.partner_reply(
            mine.id,
            reviews.ReplyIn(body="Thanks!"),
            owner_id=owner.id,
            user_id=owner.id,
            db=test_session,
        )
    ).data
    assert out.reply_body == "Thanks!"
    with pytest.raises(HTTPException) as exc:
        await reviews.partner_reply(
            foreign.id,
            reviews.ReplyIn(body="Hi"),
            owner_id=owner.id,
            user_id=owner.id,
            db=test_session,
        )
    assert exc.value.status_code == 404

    listed = (await reviews.partner_list(owner_id=owner.id, db=test_session)).data
    assert [r.id for r in listed.items] == [mine.id]


async def test_report_hide_and_dismiss(test_session):
    owner, _ = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store, other = await _store(test_session), await _store(test_session)
    await _install(test_session, app, store)
    review = await _review(test_session, app, store, rating=1)

    await reviews.merchant_report(
        app.slug,
        review.id,
        reviews.ReportIn(reason="Spam"),
        user_id=other.owner_id,
        db=test_session,
    )
    queue = (await reviews.admin_list(_=uuid4(), db=test_session)).data
    assert [(r.id, r.report_reason) for r in queue.items] == [(review.id, "Spam")]

    await reviews.admin_moderate(
        review.id, reviews.ModerateIn(action="hide"), admin_id=uuid4(), db=test_session
    )
    assert (await reviews.admin_list(_=uuid4(), db=test_session)).data.total == 0
    assert (await reviews.rating_summaries(test_session, [app.id])) == {}
    seen_by_other = (
        await reviews.merchant_list(app.slug, store=other, db=test_session)
    ).data
    assert seen_by_other.items == []
    own = (await reviews.merchant_list(app.slug, store=store, db=test_session)).data
    assert own.mine.is_hidden

    await reviews.admin_moderate(
        review.id,
        reviews.ModerateIn(action="unhide"),
        admin_id=uuid4(),
        db=test_session,
    )
    assert (await reviews.rating_summaries(test_session, [app.id]))[app.id] == (1.0, 1)


async def test_writes_are_rate_limited(test_session, monkeypatch):
    async def deny(*_a, **_k):
        return False, 99, 60

    monkeypatch.setattr(support, "_check_per_user_limit", deny)
    app = await _app(test_session)
    store = await _store(test_session)
    await _install(test_session, app, store)
    with pytest.raises(HTTPException) as exc:
        await _review(test_session, app, store)
    assert exc.value.status_code == 429


def test_admin_routes_require_admin():
    for router in (reviews.admin_router, support.admin_router):
        for r in router.routes:
            names = {d.call.__name__ for d in r.dependant.dependencies}
            assert "require_admin" in names, r.path


# ─── Support ──────────────────────────────────────────────────────


async def _open(s, store, app, mail, files=None):
    return (
        await support.merchant_create(
            store=store,
            user_id=store.owner_id,
            db=s,
            email_service=mail,
            storage=_Storage(),
            app_slug=app.slug,
            subject="Sync broken",
            body="Orders do not sync",
            files=files,
        )
    ).data


async def test_merchant_ticket_flows_between_store_and_partner(test_session):
    owner, account = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store = await _store(test_session)
    mail = _Mail()
    thread = await _open(test_session, store, app, mail)
    assert thread.ticket.status == "open" and thread.ticket.partner_name == "Acme"
    assert mail.sent[-1].to == account.support_email

    ctx = await _ctx(test_session, owner)
    listed = (await support.partner_list(ctx=ctx, db=test_session)).data
    assert [t.id for t in listed.items] == [thread.ticket.id]

    replied = (
        await support.partner_reply(
            thread.ticket.id,
            ctx=ctx,
            db=test_session,
            email_service=mail,
            storage=_Storage(),
            body="Fixed",
        )
    ).data
    assert replied.ticket.status == "answered"
    assert [m.author_role for m in replied.messages] == ["merchant", "partner"]
    owner_email = await test_session.scalar(
        UserModel.__table__.select()
        .with_only_columns(UserModel.email)
        .where(UserModel.id == store.owner_id)
    )
    assert mail.sent[-1].to == owner_email

    back = (
        await support.merchant_reply(
            thread.ticket.id,
            store=store,
            user_id=store.owner_id,
            db=test_session,
            email_service=mail,
            storage=_Storage(),
            body="Thanks",
        )
    ).data
    assert back.ticket.status == "open"
    closed = (
        await support.merchant_close(thread.ticket.id, store=store, db=test_session)
    ).data
    assert closed.ticket.status == "closed"


async def test_tickets_are_invisible_to_other_stores_and_partners(test_session):
    owner, _ = await _partner(test_session)
    other_owner, _ = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store, other_store = await _store(test_session), await _store(test_session)
    thread = await _open(test_session, store, app, _Mail())

    with pytest.raises(HTTPException) as exc:
        await support.merchant_get(thread.ticket.id, store=other_store, db=test_session)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await support.partner_get(
            thread.ticket.id, ctx=await _ctx(test_session, other_owner), db=test_session
        )
    assert exc.value.status_code == 404
    other_list = (await support.merchant_list(store=other_store, db=test_session)).data
    assert other_list.total == 0


async def test_a_team_member_sees_the_partners_tickets(test_session):
    owner, account = await _partner(test_session)
    dev = await _user(test_session)
    test_session.add(
        PartnerMemberModel(
            id=uuid4(),
            partner_id=account.id,
            user_id=dev.id,
            email=dev.email,
            role="developer",
            status="active",
        )
    )
    await test_session.flush()
    app = await _app(test_session, owner.id)
    thread = await _open(test_session, await _store(test_session), app, _Mail())
    got = (
        await support.partner_get(
            thread.ticket.id, ctx=await _ctx(test_session, dev), db=test_session
        )
    ).data
    assert got.ticket.id == thread.ticket.id


async def test_numu_apps_have_no_developer_to_contact(test_session):
    app = await _app(test_session)
    with pytest.raises(HTTPException) as exc:
        await _open(test_session, await _store(test_session), app, _Mail())
    assert exc.value.status_code == 404


async def test_attachments_accept_images_and_pdf_only(test_session):
    owner, _ = await _partner(test_session)
    app = await _app(test_session, owner.id)
    store = await _store(test_session)
    thread = await _open(
        test_session,
        store,
        app,
        _Mail(),
        files=[_file("a.pdf", b"%PDF-1.7 x"), _file("b.png", b"\x89PNG\r\n\x1a\n0000")],
    )
    kinds = [a["content_type"] for a in thread.messages[0].attachments]
    assert kinds == ["application/pdf", "image/png"]

    with pytest.raises(HTTPException) as exc:
        await _open(test_session, store, app, _Mail(), files=[_file("x.exe", b"MZ..")])
    assert exc.value.status_code == 415
    with pytest.raises(HTTPException) as exc:
        await _open(
            test_session,
            store,
            app,
            _Mail(),
            files=[_file(f"{i}.pdf", b"%PDF-1") for i in range(4)],
        )
    assert exc.value.status_code == 422


async def test_partner_to_numu_ticket_is_answered_by_staff(test_session):
    owner, account = await _partner(test_session)
    other, _ = await _partner(test_session)
    mail = _Mail()
    ctx = await _ctx(test_session, owner)
    thread = (
        await support.partner_create(
            ctx=ctx,
            db=test_session,
            email_service=mail,
            storage=_Storage(),
            subject="Payout question",
            body="When are payouts?",
        )
    ).data
    assert thread.ticket.kind == "partner"

    inbox = (await support.admin_list(_=uuid4(), db=test_session)).data
    assert [t.id for t in inbox.items] == [thread.ticket.id]
    answered = (
        await support.admin_reply(
            thread.ticket.id,
            admin_id=owner.id,
            db=test_session,
            email_service=mail,
            storage=_Storage(),
            body="Monthly",
        )
    ).data
    assert answered.ticket.status == "answered"
    assert mail.sent[-1].to == account.support_email

    with pytest.raises(HTTPException):
        await support.partner_get(
            thread.ticket.id, ctx=await _ctx(test_session, other), db=test_session
        )
    store = await _store(test_session)
    with pytest.raises(HTTPException):
        await support.merchant_get(thread.ticket.id, store=store, db=test_session)
