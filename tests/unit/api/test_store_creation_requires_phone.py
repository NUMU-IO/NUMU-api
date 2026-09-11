from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.v1.routes.stores.stores import create_store
from src.api.v1.schemas.tenant.store import CreateStoreRequest
from src.core.exceptions import ValidationError


@pytest.mark.asyncio
async def test_store_creation_rejects_an_owner_without_a_phone():
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(phone=None)
    db = AsyncMock()
    db.execute.return_value = result

    with pytest.raises(ValidationError, match="Phone number is required"):
        await create_store(
            CreateStoreRequest(name="OAuth Store", subdomain="oauth-store"),
            uuid4(),
            db,
        )
