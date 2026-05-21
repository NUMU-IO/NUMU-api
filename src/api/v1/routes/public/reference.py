"""Public reference data endpoints.

GET /api/v1/public/reference/governorates — canonical, immutable,
cache-forever (invalidated at deploy). Serves both the merchant
dashboard's governorate picker and the storefront fallback.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.shipping import GovernorateResponse
from src.core.value_objects.geography import EGYPTIAN_GOVERNORATES

router = APIRouter()


@router.get(
    "/reference/governorates",
    response_model=SuccessResponse[list[GovernorateResponse]],
    summary="Canonical governorate reference list",
    operation_id="list_reference_governorates",
)
async def list_reference_governorates(
    country: Annotated[str, Query(description="ISO country code")] = "EG",
    locale: Annotated[str, Query(description="'en' or 'ar'")] = "en",
):
    """Return the 27 Egyptian governorates.

    Currently Egypt-only; `country` is accepted for forward-compat when
    KSA / UAE come online. Unknown country → empty list.
    """
    if country.upper() != "EG":
        return SuccessResponse(data=[])

    use_ar = (locale or "en").lower().startswith("ar")
    data = [
        GovernorateResponse(
            code=g.code,
            name=g.name_ar if use_ar else g.name_en,
            name_en=g.name_en,
            name_ar=g.name_ar,
            default_zone=g.zone,
            capital=g.capital_ar if use_ar else g.capital_en,
        )
        for g in EGYPTIAN_GOVERNORATES
    ]
    data.sort(key=lambda g: g.name)
    return SuccessResponse(data=data)
