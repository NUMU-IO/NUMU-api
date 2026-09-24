"""Partner themes: a partner sees and changes only its own themes, review
decisions land in the states the portal and CLI show, and only an approved
version can be published."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.v1.routes.marketplace import developer
from src.api.v1.schemas.tenant.marketplace import (
    CreateListingRequest,
    UpdateListingRequest,
)
from src.application.services.marketplace_service import MarketplaceService
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
    MarketplaceThemeVersionModel,
)
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)


@pytest.fixture(autouse=True)
async def _sqlite_now(test_session):
    raw = await (await test_session.connection()).get_raw_connection()
    await raw.driver_connection.create_function(
        "NOW", 0, lambda: datetime.now(UTC).isoformat(" ")
    )


async def _user(s):
    u = UserModel(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
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


async def _theme(s, owner, status="draft", flags=None):
    t = MarketplaceThemeModel(
        id=uuid4(),
        developer_id=owner.id,
        name="Nile",
        name_ar="النيل",
        slug=f"nile-{uuid4().hex[:8]}",
        status=status,
        flags=flags or {},
    )
    s.add(t)
    await s.flush()
    return t


async def _version(s, theme, status="pending_review", version="1.0.0"):
    v = MarketplaceThemeVersionModel(
        id=uuid4(),
        theme_id=theme.id,
        version_string=version,
        status=status,
        lint_status="passed",
        bundle_url="https://cdn.numueg.app/nile/1.0.0/theme.js",
    )
    s.add(v)
    await s.flush()
    return v


def _svc(s):
    return MarketplaceService(marketplace_repo=MarketplaceRepository(s))


async def test_a_partner_lists_only_its_own_themes(test_session):
    owner, other = await _user(test_session), await _user(test_session)
    mine = await _theme(test_session, owner)
    await _theme(test_session, other)

    out = await developer.list_my_themes(svc=_svc(test_session), user_id=owner.id)
    assert [t.id for t in out.data] == [str(mine.id)]
    assert out.data[0].name_ar == "النيل"


async def test_a_partner_cannot_read_or_publish_another_partners_theme(
    test_session,
):
    owner, other = await _user(test_session), await _user(test_session)
    theme = await _theme(test_session, owner)
    version = await _version(test_session, theme, status="approved")
    svc = _svc(test_session)

    with pytest.raises(HTTPException) as exc:
        await developer.list_versions(theme.id, svc=svc, user_id=other.id)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await developer.publish_version(version.id, svc=svc, user_id=other.id)
    assert exc.value.status_code == 404


async def test_approve_then_publish_makes_the_theme_installable(test_session):
    owner, admin = await _user(test_session), await _user(test_session)
    theme = await _theme(test_session, owner, status="pending_review")
    version = await _version(test_session, theme)
    svc = _svc(test_session)

    with pytest.raises(HTTPException) as exc:
        await developer.publish_version(version.id, svc=svc, user_id=owner.id)
    assert exc.value.status_code == 409

    reviewed = await svc.review_version(admin.id, version.id, "approve", "nice")
    assert reviewed["status"] == "approved"
    await test_session.refresh(theme)
    assert theme.status == "pending_review"

    out = await developer.publish_version(version.id, svc=svc, user_id=owner.id)
    assert out.data.status == "published"
    await test_session.refresh(theme)
    await test_session.refresh(version)
    assert (theme.status, version.status) == ("published", "published")
    assert theme.flags["catalog_visible"] is True

    versions = await developer.list_versions(theme.id, svc=svc, user_id=owner.id)
    assert versions.data[0].review_notes == "nice"


async def test_publish_keeps_an_admin_hidden_theme_hidden(test_session):
    owner, admin = await _user(test_session), await _user(test_session)
    theme = await _theme(test_session, owner, flags={"catalog_visible": False})
    version = await _version(test_session, theme)
    svc = _svc(test_session)

    await svc.review_version(admin.id, version.id, "approve")
    await developer.publish_version(version.id, svc=svc, user_id=owner.id)
    await test_session.refresh(theme)
    assert theme.flags["catalog_visible"] is False


async def test_rejecting_a_new_version_never_unlists_a_live_theme(test_session):
    owner, admin = await _user(test_session), await _user(test_session)
    theme = await _theme(test_session, owner, status="published")
    version = await _version(test_session, theme, version="2.0.0")

    out = await _svc(test_session).review_version(admin.id, version.id, "reject")
    assert out["status"] == "rejected"
    await test_session.refresh(theme)
    assert theme.status == "published"


async def test_paid_themes_are_refused_for_now(test_session):
    owner = await _user(test_session)
    theme = await _theme(test_session, owner)
    svc = _svc(test_session)

    with pytest.raises(HTTPException) as exc:
        await developer.create_listing(
            CreateListingRequest(name="Paid", slug="paid-theme", price_cents=1000),
            svc=svc,
            user_id=owner.id,
        )
    assert exc.value.status_code == 400
    with pytest.raises(ValueError, match="coming soon"):
        await svc.update_listing(owner.id, theme.id, {"price_cents": 500})


def test_screenshots_must_come_from_an_allowed_host():
    with pytest.raises(ValidationError):
        UpdateListingRequest(screenshots=[{"url": "https://attacker.example/x.png"}])
    ok = UpdateListingRequest(
        screenshots=[{"url": "https://cdn.numueg.app/x.png", "viewport": "mobile"}]
    )
    assert ok.screenshots[0].viewport == "mobile"
