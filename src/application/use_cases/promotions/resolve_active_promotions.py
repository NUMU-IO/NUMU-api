"""ResolveActivePromotionsUseCase — the storefront hot path.

Delegates the eligibility / grouping / priority logic to the domain
service `PromotionResolver`, then maps the result into the API DTO and
attaches per-promotion fingerprints + translated copy.

A Redis cache wrapper lives in the infrastructure layer (step 04) and
will compose around this use case; this file stays cache-agnostic so
unit tests can drive it without Redis.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.application.dto.promotion import PromotionDisplayOutput
from src.application.dto.promotion_resolution import (
    ActivePromotionsOutput,
    ResolvedPromotionOutput,
    VisitorContextInput,
)
from src.application.use_cases.promotions._mapping import display_to_output
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.services.promotion_eligibility_checker import EligibilityContext
from src.core.services.promotion_resolver import (
    PromotionResolver,
    ResolvedPromotion,
)


def _fingerprint(promotion_id: UUID, version: int) -> str:
    # Stable cache fingerprint, NOT a security primitive. SHA1 is used
    # only for its stable 8-char digest; clients use it as a cheap ETag.
    h = hashlib.sha1(  # noqa: S324
        f"{promotion_id}:{version}".encode(), usedforsecurity=False
    )
    return h.hexdigest()[:8]


class ResolveActivePromotionsUseCase:
    """Returns the per-visitor active promotion set, grouped by surface."""

    def __init__(
        self,
        *,
        resolver: PromotionResolver,
        coupon_repo: ICouponRepository,
    ) -> None:
        self._resolver = resolver
        self._coupon_repo = coupon_repo

    async def execute(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        visitor: VisitorContextInput,
    ) -> ActivePromotionsOutput:
        ctx = EligibilityContext(
            customer_id=visitor.customer_id,
            customer_tags=visitor.customer_tags,
            cart_subtotal_cents=visitor.cart_subtotal_cents,
            cart_product_ids=visitor.cart_product_ids,
            cart_category_ids=visitor.cart_category_ids,
            country=visitor.country,
            city=visitor.city,
            device=visitor.device,
            is_first_visit=visitor.is_first_visit,
            is_logged_in=visitor.is_logged_in,
        )

        resolved = await self._resolver.resolve_active_for_visitor(
            store_id=store_id,
            context=ctx,
            page_path=visitor.page_path,
        )

        async def _bucket(
            items: list[ResolvedPromotion],
        ) -> list[ResolvedPromotionOutput]:
            return [await self._to_output(r, visitor.locale) for r in items]

        cookie_out = (
            await self._to_output(resolved.cookie_banner, visitor.locale)
            if resolved.cookie_banner is not None
            else None
        )

        return ActivePromotionsOutput(
            announcement_bars=await _bucket(resolved.announcement_bars),
            popups=await _bucket(resolved.popups),
            floating_widgets=await _bucket(resolved.floating_widgets),
            cookie_banner=cookie_out,
            auto_discounts=await _bucket(resolved.auto_discounts),
            discount_codes_visible=await _bucket(resolved.discount_codes_visible),
            resolved_at=datetime.now(UTC),
        )

    async def _to_output(
        self, item: ResolvedPromotion, locale: str
    ) -> ResolvedPromotionOutput:
        promo: Promotion = item.promotion
        translated = self._pick_translation(promo, locale)
        return ResolvedPromotionOutput(
            promotion_id=promo.id,
            surface=promo.surface,
            priority=promo.priority,
            content=promo.content.model_dump(),
            translated_content=translated,
            discount_rule=promo.discount_rule,
            coupon_code=await self._maybe_coupon_code(promo),
            display=self._display_out(item.display),
            fingerprint=_fingerprint(promo.id, promo.version),
        )

    @staticmethod
    def _pick_translation(promo: Promotion, locale: str) -> dict[str, Any]:
        # Prefer requested locale; fall back to en, then ar; empty if neither.
        for candidate in (locale, "en", "ar"):
            if candidate in promo.translations:
                return promo.translations[candidate].model_dump(exclude_none=True)
        return {}

    @staticmethod
    def _display_out(d: PromotionDisplay | None) -> PromotionDisplayOutput | None:
        return display_to_output(d) if d is not None else None

    async def _maybe_coupon_code(self, promo: Promotion) -> str | None:
        # Only `discount_code` surfaces have a coupon. A coupon code is
        # only revealed for `discount_codes_visible` callers — but we
        # populate it on the resolved row regardless and let the
        # surface filter in the storefront handle visibility.
        if promo.coupon_id is None:
            return None
        coupon = await self._coupon_repo.get_by_id(promo.coupon_id)
        return coupon.code if coupon else None
