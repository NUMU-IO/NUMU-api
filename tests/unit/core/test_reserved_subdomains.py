"""Stores can't take NUMU's own hostnames (audit D13)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.api.v1.schemas.public.tenant import CreateTenantRequest
from src.application.use_cases.stores.create_store import validate_subdomain
from src.core.exceptions import ValidationError
from src.infrastructure.database.models.public.tenant import TenantModel


@pytest.mark.parametrize(
    "name",
    [
        "merchant",
        "partners",
        "trust",
        "wa",
        "status",
        "auth",
        "api-test",
        "Merchant-Staging",
    ],
)
def test_platform_hosts_are_reserved(name):
    with pytest.raises(ValidationError):
        validate_subdomain(name)


def test_ordinary_names_pass():
    assert validate_subdomain("beit-el-khazaf") == "beit-el-khazaf"


def test_tenant_route_uses_the_same_list():
    with pytest.raises(PydanticValidationError):
        CreateTenantRequest(name="x", subdomain="trust")


def test_trial_days_round_up():
    tenant = TenantModel(expires_at=datetime.now(UTC) + timedelta(days=37, minutes=-15))
    assert tenant.days_remaining == 37
    tenant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert tenant.days_remaining == 0
