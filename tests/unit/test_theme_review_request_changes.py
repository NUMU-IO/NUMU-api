"""Theme review accepts request_changes (the admin UI always sent it and got
a 400). It closes the version but, unlike reject, leaves the listing in draft
so the developer can resubmit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.schemas.tenant.marketplace import ReviewDecisionRequest
from src.application.services.marketplace_service import MarketplaceService
from src.core.entities.marketplace_theme import (
    MarketplaceThemeStatus,
    MarketplaceVersionStatus,
)


def _service(theme_status):
    version = SimpleNamespace(
        id=uuid4(),
        theme_id=uuid4(),
        status=MarketplaceVersionStatus.PENDING_REVIEW,
        version_string="1.2.0",
        lint_status="passed",
    )
    theme = SimpleNamespace(id=version.theme_id, status=theme_status)
    repo = SimpleNamespace(
        get_version_by_id=AsyncMock(return_value=version),
        get_theme_by_id=AsyncMock(return_value=theme),
        update_version=AsyncMock(),
        update_theme=AsyncMock(),
    )
    return MarketplaceService(marketplace_repo=repo), repo, version


def test_the_schema_accepts_request_changes():
    assert (
        ReviewDecisionRequest(decision="request_changes").decision == "request_changes"
    )


@pytest.mark.asyncio
async def test_request_changes_closes_the_version_and_reopens_the_listing():
    svc, repo, version = _service(MarketplaceThemeStatus.PENDING_REVIEW)

    result = await svc.review_version(uuid4(), version.id, "request_changes", "fix RTL")

    assert result["status"] == MarketplaceVersionStatus.CHANGES_REQUESTED.value
    update = repo.update_version.await_args.args[1]
    assert update["review_notes"] == "fix RTL"
    repo.update_theme.assert_awaited_once_with(
        version.theme_id, {"status": MarketplaceThemeStatus.DRAFT.value}
    )


@pytest.mark.asyncio
async def test_request_changes_never_unpublishes_a_live_theme():
    svc, repo, version = _service(MarketplaceThemeStatus.PUBLISHED)

    await svc.review_version(uuid4(), version.id, "request_changes", "fix RTL")

    repo.update_theme.assert_not_awaited()
