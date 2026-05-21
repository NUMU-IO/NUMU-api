"""Storefront currency routes — Phase 6.

Read-only display rates for multi-currency presentment. The store's
**capture** currency (what the gateway charges) is unchanged; this
endpoint feeds the SDK's `<Money>` so visitors can see prices in their
preferred display currency.

URL:
    GET /storefront/store/{store_id}/currencies
        → { base, default_presentment, presentment, rates: { TARGET: rate } }

Per-store settings (in store.settings JSONB):
    presentment_currencies: ["EGP", "USD", "EUR"]   # offered list
    default_presentment_currency: "EGP"             # initial selection
    auto_convert: true                              # show converted prices

When the store doesn't opt in (default), this returns the store's
base currency only — themes degrade to single-currency rendering
without needing a feature flag.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.currency_service import CurrencyService
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import StoreRepository

router = APIRouter()


class CurrencyConfigResponse(BaseModel):
    base: str
    default_presentment: str
    presentment: list[str]
    rates: dict[str, str]  # serialize Decimal as string for precision
    auto_convert: bool


@router.get(
    "/currencies",
    response_model=SuccessResponse[CurrencyConfigResponse],
    summary="Get presentment currencies + rates",
    operation_id="get_currency_config",
)
async def get_currency_config(
    store_id: UUID,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    settings = getattr(store, "settings", {}) or {}
    base = getattr(store, "currency", None) or settings.get("currency") or "EGP"
    presentment_raw = settings.get("presentment_currencies") or [base]
    # De-dupe and force the base currency to be present so themes can
    # always render in the merchant's home currency without an extra
    # branch — even if the merchant accidentally omits it.
    presentment = list(dict.fromkeys([base, *presentment_raw]))
    default_presentment = settings.get("default_presentment_currency") or base
    auto_convert = bool(settings.get("auto_convert", False))

    rates: dict[str, str] = {base: "1"}
    if len(presentment) > 1:
        async with AsyncSessionLocal() as session:
            svc = CurrencyService(session)
            for tgt in presentment:
                if tgt == base:
                    continue
                rate = await svc.get_rate(base, tgt)
                # Missing rate → omit from the map. Theme falls back
                # to base currency display for that target — better
                # than a wrong number.
                if rate is not None:
                    rates[tgt] = str(rate)

    return SuccessResponse(
        data=CurrencyConfigResponse(
            base=base,
            default_presentment=default_presentment,
            presentment=presentment,
            rates=rates,
            auto_convert=auto_convert,
        ),
        message="Currency config resolved",
    )
