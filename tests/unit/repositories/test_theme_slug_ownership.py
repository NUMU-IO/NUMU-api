"""A theme.json id must not let a store write into a theme it does not own."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.core.exceptions.base import EntityAlreadyExistsError
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
)
from src.infrastructure.database.models.tenant.theme import ThemeModel
from src.infrastructure.repositories.theme_repository import ThemeRepository


async def _theme(repo: ThemeRepository, slug: str, created_by=None) -> ThemeModel:
    model = ThemeModel(
        id=uuid4(), name=slug, slug=slug, type="external", created_by=created_by
    )
    repo.session.add(model)
    await repo.session.flush()
    return model


@pytest.mark.asyncio
async def test_slug_ownership(test_session):
    repo = ThemeRepository(test_session)
    store_a, store_b = uuid4(), uuid4()
    await _theme(repo, "empire-v3")
    owned = await _theme(repo, "mine", created_by=store_a)
    legacy = await _theme(repo, f"empire-v3-{store_a.hex[:8]}", created_by=uuid4())

    with pytest.raises(EntityAlreadyExistsError):
        await repo.get_by_slug_for_owner("empire-v3", store_a)
    with pytest.raises(EntityAlreadyExistsError):
        await repo.get_by_slug_for_owner("mine", store_b)
    with pytest.raises(EntityAlreadyExistsError):
        await repo.get_by_slug_for_owner(legacy.slug, store_b)

    assert (await repo.get_by_slug_for_owner("mine", store_a)).id == owned.id
    assert (await repo.get_by_slug_for_owner(legacy.slug, store_a)).id == legacy.id
    assert await repo.get_by_slug_for_owner("brand-new", store_a) is None


@pytest.mark.asyncio
async def test_marketplace_slug_is_never_free(test_session):
    test_session.add(
        MarketplaceThemeModel(
            id=uuid4(),
            developer_id=uuid4(),
            name="Listed",
            slug="listed-theme",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await test_session.flush()

    with pytest.raises(EntityAlreadyExistsError):
        await ThemeRepository(test_session).get_by_slug_for_owner(
            "listed-theme", uuid4()
        )
