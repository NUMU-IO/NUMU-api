"""PromotionResolver — orchestrates 'what do we show this visitor right now?'.

Used by the storefront API (step 06) to answer a single read with the
final list of active promotions grouped by surface, after eligibility
filtering and priority sorting.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionSurface
from src.core.interfaces.repositories.promotion_dismissal_repository import (
    IPromotionDismissalRepository,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
)
from src.core.services.promotion_eligibility_checker import (
    EligibilityContext,
    PromotionEligibilityChecker,
)


@dataclass(frozen=True)
class ResolvedPromotion:
    """A promotion + its applicable display for one visitor + page."""

    promotion: Promotion
    display: PromotionDisplay | None
    targets: list[PromotionTarget] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedPromotions:
    """Active promos for one visitor, grouped by surface."""

    announcement_bars: list[ResolvedPromotion] = field(default_factory=list)
    popups: list[ResolvedPromotion] = field(default_factory=list)
    floating_widgets: list[ResolvedPromotion] = field(default_factory=list)
    cookie_banner: ResolvedPromotion | None = None
    auto_discounts: list[ResolvedPromotion] = field(default_factory=list)
    discount_codes_visible: list[ResolvedPromotion] = field(default_factory=list)


class PromotionResolver:
    """Service that the storefront API calls per-request."""

    def __init__(
        self,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        dismissal_repo: IPromotionDismissalRepository,
        eligibility_checker: PromotionEligibilityChecker,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._dismissal_repo = dismissal_repo
        self._checker = eligibility_checker

    async def resolve_active_for_visitor(
        self,
        store_id: UUID,
        context: EligibilityContext,
        page_path: str,
        *,
        now: datetime | None = None,
        preview: bool = False,
    ) -> ResolvedPromotions:
        """Resolve the visible promotion set for one visitor + page.

        `preview=True` is set by the merchant builder iframe (validated
        upstream by the storefront route from `X-Preview-Token`). In
        preview mode the repo also returns DRAFT / SCHEDULED / PAUSED
        rows so the merchant can see un-published changes before going
        live; eligibility / dismissal filtering still runs so what they
        see matches what real visitors will see.
        """
        moment = now or datetime.now(UTC)

        # 1. Load active (or active+draft for preview) promotions —
        #    displays/targets are eagerly loaded by the infra repo.
        promotions = await self._promotion_repo.list_active_for_storefront(
            store_id, moment, include_drafts=preview
        )

        # 2. Hydrate dismissals once per call.
        dismissed_ids = await self._dismissal_repo.list_dismissed_promotion_ids(
            store_id,
            customer_id=context.customer_id,
            visitor_token=None,  # repo accepts a token via context if we choose to wire it later
        )
        ctx = EligibilityContext(
            customer_id=context.customer_id,
            customer_tags=context.customer_tags,
            cart_subtotal_cents=context.cart_subtotal_cents,
            cart_product_ids=context.cart_product_ids,
            cart_category_ids=context.cart_category_ids,
            country=context.country,
            city=context.city,
            device=context.device,
            is_first_visit=context.is_first_visit,
            is_logged_in=context.is_logged_in,
            dismissed_promotion_ids=context.dismissed_promotion_ids | dismissed_ids,
        )

        # Step 08 N+1 fix: bulk-fetch all displays + targets once,
        # then index by promotion_id. Previous code looped
        # ``list_for_promotion`` per promotion, which fanned out to
        # ``2 * len(promotions)`` extra queries per resolve call.
        promo_ids = [p.id for p in promotions]
        displays_by_promo = await self._display_repo.list_for_promotions(promo_ids)
        targets_by_promo = await self._target_repo.list_for_promotions(promo_ids)

        # 3. Filter & group by surface, applying display rules.
        bucket_bars: list[ResolvedPromotion] = []
        bucket_popups: list[ResolvedPromotion] = []
        bucket_widgets: list[ResolvedPromotion] = []
        bucket_cookie: list[ResolvedPromotion] = []
        bucket_auto: list[ResolvedPromotion] = []
        bucket_codes: list[ResolvedPromotion] = []

        for promo in promotions:
            displays = displays_by_promo.get(promo.id, [])
            targets = targets_by_promo.get(promo.id, [])

            verdict = self._checker.is_eligible(promo, targets, ctx, now=moment)
            if not verdict.eligible:
                continue

            display = self._first_matching_display(displays, page_path, ctx.device)
            if display is None and promo.surface not in {
                PromotionSurface.DISCOUNT_CODE,
                PromotionSurface.AUTOMATIC,
            }:
                # Visual surfaces require a display rule.
                continue

            resolved = ResolvedPromotion(
                promotion=promo, display=display, targets=list(targets)
            )

            match promo.surface:
                case PromotionSurface.ANNOUNCEMENT_BAR:
                    bucket_bars.append(resolved)
                case PromotionSurface.POPUP:
                    bucket_popups.append(resolved)
                case PromotionSurface.FLOATING_WIDGET:
                    bucket_widgets.append(resolved)
                case PromotionSurface.COOKIE_BANNER:
                    bucket_cookie.append(resolved)
                case PromotionSurface.AUTOMATIC:
                    bucket_auto.append(resolved)
                case PromotionSurface.DISCOUNT_CODE:
                    bucket_codes.append(resolved)

        # 4. Priority-order each bucket (higher priority first).
        sort_key = lambda r: (-r.promotion.priority, r.promotion.created_at)  # noqa: E731
        bucket_bars.sort(key=sort_key)
        bucket_popups.sort(key=sort_key)
        bucket_widgets.sort(key=sort_key)
        bucket_cookie.sort(key=sort_key)
        bucket_auto.sort(key=sort_key)
        bucket_codes.sort(key=sort_key)

        cookie_winner = bucket_cookie[0] if bucket_cookie else None

        return ResolvedPromotions(
            announcement_bars=bucket_bars,
            popups=bucket_popups,
            floating_widgets=bucket_widgets,
            cookie_banner=cookie_winner,
            auto_discounts=bucket_auto,
            discount_codes_visible=bucket_codes,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _first_matching_display(
        displays: list[PromotionDisplay], page_path: str, device: str
    ) -> PromotionDisplay | None:
        for d in displays:
            if not d.is_enabled:
                continue
            if not d.matches_page(page_path):
                continue
            if not d.matches_device(device):
                continue
            return d
        return None
