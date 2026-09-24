"""Partner portal: team access, dashboard, and webhook deliveries.

The ownership rules are the point: a partner sees and resends only the
deliveries of apps it owns, a team member acts for the partner that invited
them, and only an owner or admin manages the team.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.dependencies.partners import partner_context, require_partner_manager
from src.api.v1.routes import partner_portal as portal
from src.api.v1.routes.partners import get_me
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
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.webhook import (
    WebhookDeliveryLogModel,
    WebhookSubscriptionModel,
)


async def _user(s, email=None):
    u = UserModel(
        id=uuid4(),
        email=email or f"u-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="Test",
        last_name="User",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    s.add(u)
    await s.flush()
    return u


async def _partner(s, owner):
    p = PartnerAccountModel(
        id=uuid4(),
        user_id=owner.id,
        kind="company",
        display_name="Acme",
        country="EG",
        support_email=owner.email,
        status="approved",
        agreement_version="2026-09-draft",
    )
    s.add(p)
    await s.flush()
    return p


async def _app_with_delivery(s, owner, status="exhausted", enabled=True):
    app = AppModel(
        id=uuid4(),
        slug=f"a-{uuid4().hex[:8]}",
        name="Acme App",
        developer_id=owner.id,
        status=AppStatus.PUBLISHED,
        manifest={},
        listing_flags={},
    )
    s.add(app)
    await s.flush()
    tenant_id, store_id = uuid4(), uuid4()
    inst = AppInstallationModel(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        app_id=app.id,
        is_enabled=enabled,
        settings={},
        status="active",
        granted_scopes=[],
    )
    s.add(inst)
    await s.flush()
    sub = WebhookSubscriptionModel(
        id=uuid4(),
        tenant_id=tenant_id,
        store_id=store_id,
        url="https://example.com/hook",
        events=["order.created"],
        secret="s",
        is_active=True,
        app_installation_id=inst.id,
    )
    s.add(sub)
    await s.flush()
    log = WebhookDeliveryLogModel(
        id=uuid4(),
        tenant_id=tenant_id,
        subscription_id=sub.id,
        store_id=store_id,
        event_type="order.created",
        event_id=uuid4(),
        payload={},
        status=status,
        attempt_count=6,
        exhausted_at=datetime.now(UTC),
    )
    s.add(log)
    await s.flush()
    return app, log


async def _ctx(s, user):
    return await partner_context(user=(user.id, "store_owner"), db=s)


async def _member(s, partner, user, role, status="active"):
    m = PartnerMemberModel(
        id=uuid4(),
        partner_id=partner.id,
        user_id=user.id if status == "active" else None,
        email=user.email,
        role=role,
        status=status,
    )
    s.add(m)
    await s.flush()
    return m


# ─── Access ───────────────────────────────────────────────────────


async def test_a_member_acts_for_the_partner_that_invited_them(test_session):
    owner, dev = await _user(test_session), await _user(test_session)
    partner = await _partner(test_session, owner)
    await _member(test_session, partner, dev, "developer")
    ctx = await _ctx(test_session, dev)
    assert ctx.owner_id == owner.id
    assert ctx.role == "developer"


async def test_an_invited_but_not_accepted_member_is_not_a_partner(test_session):
    owner, dev = await _user(test_session), await _user(test_session)
    partner = await _partner(test_session, owner)
    await _member(test_session, partner, dev, "admin", status="invited")
    with pytest.raises(HTTPException) as exc:
        await _ctx(test_session, dev)
    assert exc.value.status_code == 404


async def test_a_developer_cannot_manage_the_team(test_session):
    owner, dev = await _user(test_session), await _user(test_session)
    partner = await _partner(test_session, owner)
    await _member(test_session, partner, dev, "developer")
    with pytest.raises(HTTPException) as exc:
        await require_partner_manager(await _ctx(test_session, dev))
    assert exc.value.status_code == 403
    assert (
        await require_partner_manager(await _ctx(test_session, owner))
    ).role == "owner"


# ─── Dashboard ────────────────────────────────────────────────────


async def test_the_dashboard_counts_only_the_partners_own_installs(test_session):
    owner, other = await _user(test_session), await _user(test_session)
    await _partner(test_session, owner)
    await _partner(test_session, other)
    app, _ = await _app_with_delivery(test_session, owner)
    await _app_with_delivery(test_session, owner, enabled=False)
    await _app_with_delivery(test_session, other)
    test_session.add(AppUninstallEventModel(app_id=app.id, store_id=uuid4()))
    await test_session.flush()

    out = (await portal.dashboard(owner_id=owner.id, db=test_session)).data
    assert (out.installs_total, out.active, out.disabled, out.uninstalled) == (
        2,
        1,
        1,
        1,
    )
    assert sum(b.installs for b in out.monthly) == 2
    assert {i.app_name for i in out.latest} == {"Acme App"}

    one = (
        await portal.dashboard(owner_id=owner.id, db=test_session, app_id=app.id)
    ).data
    assert one.installs_total == 1


# ─── Webhook deliveries ───────────────────────────────────────────


async def test_a_partner_lists_only_deliveries_of_its_own_apps(test_session):
    owner, other = await _user(test_session), await _user(test_session)
    _, mine = await _app_with_delivery(test_session, owner)
    await _app_with_delivery(test_session, other)

    page = (await portal.list_deliveries(owner_id=owner.id, db=test_session)).data
    assert [d.id for d in page.items] == [mine.id]
    assert page.total == 1

    filtered = await portal.list_deliveries(
        owner_id=owner.id, db=test_session, event="order.updated"
    )
    assert filtered.data.total == 0


async def test_a_partner_cannot_resend_another_partners_delivery(test_session):
    owner, other = await _user(test_session), await _user(test_session)
    _, theirs = await _app_with_delivery(test_session, other)
    with pytest.raises(HTTPException) as exc:
        await portal.resend_delivery(theirs.id, owner_id=owner.id, db=test_session)
    assert exc.value.status_code == 404
    assert theirs.status == "exhausted"


async def test_resend_requeues_through_the_retry_poller(test_session):
    owner = await _user(test_session)
    _, log = await _app_with_delivery(test_session, owner)
    out = (
        await portal.resend_delivery(log.id, owner_id=owner.id, db=test_session)
    ).data
    assert out.status == "pending"
    assert log.next_attempt_at is not None and log.exhausted_at is None
    with pytest.raises(HTTPException) as exc:
        await portal.resend_delivery(log.id, owner_id=owner.id, db=test_session)
    assert exc.value.status_code == 409


# ─── Team ─────────────────────────────────────────────────────────


class _Mail:
    def __init__(self):
        self.sent = []

    async def send_staff_invitation_email(self, **kw):
        self.sent.append(kw)
        return True


async def test_invite_accept_and_remove(test_session):
    owner = await _user(test_session)
    await _partner(test_session, owner)
    mail = _Mail()
    ctx = await require_partner_manager(await _ctx(test_session, owner))
    invited = (
        await portal.invite_member(
            portal.InviteRequest(email="Dev@Example.com", role="developer"),
            ctx=ctx,
            db=test_session,
            email_service=mail,
        )
    ).data
    assert invited.status == "invited"
    assert mail.sent[0]["email"] == "dev@example.com"

    with pytest.raises(HTTPException) as dup:
        await portal.invite_member(
            portal.InviteRequest(email="dev@example.com", role="admin"),
            ctx=ctx,
            db=test_session,
            email_service=mail,
        )
    assert dup.value.status_code == 409

    stranger = await _user(test_session)
    with pytest.raises(HTTPException) as wrong:
        await portal.accept_invitation(invited.id, user_id=stranger.id, db=test_session)
    assert wrong.value.status_code == 404

    dev = await _user(test_session, email="dev@example.com")
    me = (await get_me(user_id=dev.id, db=test_session)).data
    assert [i.partner_name for i in me.invitations] == ["Acme"]
    await portal.accept_invitation(invited.id, user_id=dev.id, db=test_session)
    assert (await _ctx(test_session, dev)).owner_id == owner.id

    team = (await portal.list_team(ctx=ctx, db=test_session)).data
    assert [(m.role, m.status) for m in team] == [
        ("owner", "active"),
        ("developer", "active"),
    ]

    await portal.update_member(
        invited.id, portal.RoleRequest(role="admin"), ctx=ctx, db=test_session
    )
    assert (await _ctx(test_session, dev)).role == "admin"

    await portal.remove_member(invited.id, ctx=ctx, db=test_session)
    with pytest.raises(HTTPException):
        await _ctx(test_session, dev)


async def test_a_manager_cannot_touch_another_partners_member(test_session):
    owner, other_owner, dev = (
        await _user(test_session),
        await _user(test_session),
        await _user(test_session),
    )
    await _partner(test_session, owner)
    other = await _partner(test_session, other_owner)
    theirs = await _member(test_session, other, dev, "developer")
    ctx = await require_partner_manager(await _ctx(test_session, owner))
    with pytest.raises(HTTPException) as exc:
        await portal.remove_member(theirs.id, ctx=ctx, db=test_session)
    assert exc.value.status_code == 404


async def test_an_expired_invitation_cannot_be_accepted(test_session):
    owner = await _user(test_session)
    partner = await _partner(test_session, owner)
    dev = await _user(test_session)
    m = await _member(test_session, partner, dev, "developer", status="invited")
    m.created_at = datetime.now(UTC) - timedelta(days=8)
    await test_session.flush()
    with pytest.raises(HTTPException) as exc:
        await portal.accept_invitation(m.id, user_id=dev.id, db=test_session)
    assert exc.value.status_code == 410


def test_every_team_write_needs_a_manager():
    writes = {
        r.operation_id: {d.call.__name__ for d in r.dependant.dependencies}
        for r in portal.router.routes
        if r.operation_id
        in {"invite_partner_member", "update_partner_member", "remove_partner_member"}
    }
    assert len(writes) == 3
    for op, names in writes.items():
        assert "require_partner_manager" in names, op


def _at(month, day):
    return datetime(2026, month, day, 12, tzinfo=UTC)


async def test_analytics_rebuilds_installed_stores_churn_and_reasons(
    test_session, monkeypatch
):
    owner, other = await _user(test_session), await _user(test_session)
    await _partner(test_session, owner)
    await _partner(test_session, other)
    app, _ = await _app_with_delivery(test_session, owner)
    theirs, _ = await _app_with_delivery(test_session, other)
    inst = (
        await test_session.execute(
            portal.select(AppInstallationModel).where(
                AppInstallationModel.app_id == app.id
            )
        )
    ).scalar_one()
    inst.created_at = _at(7, 5)
    test_session.add_all([
        AppUninstallEventModel(
            app_id=app.id,
            store_id=uuid4(),
            installed_at=_at(7, 10),
            created_at=_at(8, 15),
            reason="too_expensive",
            reason_text="pricey",
        ),
        AppUninstallEventModel(app_id=app.id, store_id=uuid4(), created_at=_at(9, 2)),
        AppUninstallEventModel(
            app_id=theirs.id, store_id=uuid4(), created_at=_at(8, 3), reason="bugs"
        ),
    ])
    await test_session.flush()
    seen = {}

    async def hourly(ids, since, until):
        seen["ids"] = ids
        return {str(app.id): {"n": 10, "4xx": 2, "5xx": 1}}

    monkeypatch.setattr(portal, "app_hourly", hourly)
    monkeypatch.setattr(portal, "datetime", _Frozen)

    out = (
        await portal.analytics(
            owner_id=owner.id,
            db=test_session,
            start=_at(7, 1).date(),
            end=_at(9, 30).date(),
        )
    ).data

    assert [
        (m.month, m.installs, m.uninstalls, m.active_stores) for m in out.months
    ] == [
        ("2026-07", 2, 0, 3),
        ("2026-08", 0, 1, 2),
        ("2026-09", 0, 1, 1),
    ]
    assert [m.churn_rate for m in out.months] == [0.0, 0.3333, 0.5]
    assert {r.reason: r.count for r in out.reasons} == {
        "too_expensive": 1,
        "unspecified": 1,
    }
    assert [n.text for n in out.notes] == ["pricey"]
    assert out.trial_to_paid is None and out.trial_note == "no_trial_marker"
    assert seen["ids"] == [str(app.id)]
    assert [(a.app_id, a.requests, a.error_rate) for a in out.api] == [
        (app.id, 10, 0.3)
    ]


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 24, 12, tzinfo=tz)


async def test_api_logs_are_the_partners_own_and_filterable(test_session, monkeypatch):
    owner, other = await _user(test_session), await _user(test_session)
    await _partner(test_session, owner)
    await _partner(test_session, other)
    app, _ = await _app_with_delivery(test_session, owner)
    theirs, _ = await _app_with_delivery(test_session, other)
    now = datetime.now(UTC).timestamp()
    store = str(uuid4())
    log = [
        {
            "t": now - 1,
            "id": "r1",
            "m": "GET",
            "r": "/api/v1/stores/{store_id}/orders",
            "s": 429,
            "ms": 3.0,
            "st": store,
        },
        {
            "t": now - 2,
            "id": "r2",
            "m": "GET",
            "r": "/api/v1/stores/{store_id}/orders",
            "s": 500,
            "ms": 900.0,
            "st": store,
        },
        {
            "t": now - 3,
            "id": "r3",
            "m": "POST",
            "r": "/api/v1/stores/{store_id}/products",
            "s": 404,
            "ms": 20.0,
            "st": str(uuid4()),
        },
        {
            "t": now - 4,
            "id": "r4",
            "m": "GET",
            "r": "/api/v1/stores/{store_id}/orders",
            "s": 200,
            "ms": 10.0,
            "st": store,
        },
        {
            "t": now - 90000,
            "id": "old",
            "m": "GET",
            "r": "/x",
            "s": 200,
            "ms": 1.0,
            "st": store,
        },
    ]
    asked = []

    async def entries(app_id):
        asked.append(app_id)
        return log

    async def hourly(ids, since, until):
        return {ids[0]: {"n": 4, "4xx": 2, "5xx": 1, "429": 1, "b0": 3, "b5": 1}}

    monkeypatch.setattr(portal, "app_log_entries", entries)
    monkeypatch.setattr(portal, "app_hourly", hourly)

    with pytest.raises(HTTPException) as exc:
        await portal.api_logs(app_id=theirs.id, owner_id=owner.id, db=test_session)
    assert exc.value.status_code == 404 and asked == []

    out = (
        await portal.api_logs(app_id=app.id, owner_id=owner.id, db=test_session)
    ).data
    assert [i.request_id for i in out.items] == ["r1", "r2", "r3", "r4"]
    assert out.items[0].rate_limited and not out.items[1].rate_limited
    assert (
        out.stats.requests,
        out.stats.error_rate,
        out.stats.p95_ms,
        out.stats.rate_limited,
    ) == (4, 0.75, 1000, 1)
    assert "/x" not in out.routes

    four = (
        await portal.api_logs(
            app_id=app.id, owner_id=owner.id, db=test_session, status_class="4xx"
        )
    ).data
    assert [i.request_id for i in four.items] == ["r1", "r3"]
    mine = (
        await portal.api_logs(
            app_id=app.id,
            owner_id=owner.id,
            db=test_session,
            store_id=store,
            route="/api/v1/stores/{store_id}/orders",
            page_size=2,
        )
    ).data
    assert ([i.request_id for i in mine.items], mine.total) == (["r1", "r2"], 3)
