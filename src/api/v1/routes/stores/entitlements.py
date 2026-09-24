"""What this store may use, and how much of it is used.

URL: /stores/{store_id}/entitlements

The hub gates its UI on these; the API enforces the same answers on its own,
so nothing here is a security boundary.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies.entitlements import get_entitlements, store_tenant
from src.api.responses import SuccessResponse
from src.application.services.entitlement_service import (
    PUBLIC_FIELDS,
    EntitlementService,
)
from src.infrastructure.database.models.public.tenant import TenantModel

router = APIRouter(prefix="/{store_id}/entitlements")


@router.get(
    "",
    response_model=SuccessResponse[dict],
    summary="Get store entitlements",
    operation_id="get_store_entitlements",
)
async def get_store_entitlements(
    tenant: Annotated[TenantModel, Depends(store_tenant)],
    ents: Annotated[EntitlementService, Depends(get_entitlements)],
) -> SuccessResponse[dict]:
    """Every feature's value and availability, plus the release flags that
    are on. Cached; never counts usage. Override ids stay server-side, and
    flags that are off are not listed, so a merchant never learns about a
    release they are not in."""
    snap = await ents.snapshot(tenant)
    return SuccessResponse(
        data={
            "plan": tenant.plan,
            "features": {
                key: {field: state.get(field) for field in PUBLIC_FIELDS}
                for key, state in snap["features"].items()
            },
            "flags": snap["flags"],
        },
        message="Entitlements retrieved",
    )


@router.get(
    "/usage",
    response_model=SuccessResponse[dict],
    summary="Get store usage against its limits",
    operation_id="get_store_entitlement_usage",
)
async def get_store_usage(
    tenant: Annotated[TenantModel, Depends(store_tenant)],
    ents: Annotated[EntitlementService, Depends(get_entitlements)],
) -> SuccessResponse[dict]:
    """Used, limit and reset time for every metered feature. Counts rows, so
    the hub asks only on the Billing page and next to limit banners."""
    snap = await ents.snapshot(tenant)
    metered = [key for key, state in snap["features"].items() if state.get("usage")]
    return SuccessResponse(
        data={"usage": [await ents.usage(tenant, key) for key in metered]},
        message="Usage retrieved",
    )
