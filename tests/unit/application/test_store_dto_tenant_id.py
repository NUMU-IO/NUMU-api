"""Regression — StoreDTO must expose tenant_id.

The payg plan-intent activation in the create-store route reads
``result.tenant_id`` off the DTO returned by CreateStoreUseCase. The DTO
originally lacked the field, so every payg-intent signup 500'd on prod
(AttributeError) before the activation's best-effort guard could catch it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.application.dto.store import StoreDTO


def _entity(**overrides):
    base = {
        "id": uuid4(),
        "name": "Shop",
        "slug": "shop",
        "subdomain": "shop",
        "custom_domain": None,
        "store_url": "https://shop.numueg.app",
        "owner_id": uuid4(),
        "description": None,
        "logo_url": None,
        "banner_url": None,
        "status": SimpleNamespace(value="active"),
        "default_currency": SimpleNamespace(value="EGP"),
        "country": "EG",
        "default_language": "ar",
        "contact_email": None,
        "contact_phone": None,
        "address": {},
        "social_links": {},
        "settings": {},
        "theme_settings": {},
        "business_hours": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "tenant_id": uuid4(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_from_entity_carries_tenant_id():
    entity = _entity()
    dto = StoreDTO.from_entity(entity)
    assert dto.tenant_id == entity.tenant_id


def test_from_entity_tolerates_missing_tenant_id():
    entity = _entity()
    del entity.tenant_id
    dto = StoreDTO.from_entity(entity)
    assert dto.tenant_id is None
