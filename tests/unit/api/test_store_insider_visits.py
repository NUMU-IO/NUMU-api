"""A merchant browsing their own storefront is not counted as a visitor."""

from types import SimpleNamespace
from uuid import uuid4

from src.api.v1.routes.storefront.tracking import is_store_insider
from src.core.entities.user import User, UserRole
from src.core.value_objects.email import Email
from src.infrastructure.external_services.token_service import token_service


def _token(user_id, tenant_id=None):
    user = User(
        id=user_id,
        email=Email(value="owner@example.com"),
        hashed_password="x",
        first_name="Owner",
        last_name="User",
        role=UserRole.STORE_OWNER,
    )
    return token_service.create_access_token(user, tenant_id=tenant_id)


def test_owner_and_staff_are_insiders_shoppers_are_not():
    owner_id, tenant_id = uuid4(), uuid4()
    store = SimpleNamespace(owner_id=owner_id, tenant_id=tenant_id)

    assert is_store_insider(_token(owner_id), store)
    assert is_store_insider(_token(uuid4(), tenant_id=tenant_id), store)
    assert not is_store_insider(_token(uuid4()), store)
    assert not is_store_insider(None, store)
    assert not is_store_insider("not-a-jwt", store)
