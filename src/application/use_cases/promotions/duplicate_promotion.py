"""DuplicatePromotionUseCase — clone an existing promo as a draft."""

from uuid import UUID, uuid4

from src.application.dto.promotion import PromotionOutput
from src.application.use_cases.promotions._mapping import promotion_to_output
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus
from src.core.events.base import EventBus
from src.core.events.promotion_events import PromotionCreatedEvent
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    PromotionNotFound,
)
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
    IPromotionTranslationRepository,
)


class DuplicatePromotionUseCase:
    """Clone a promotion + its child rows. Result is always `draft`."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        translation_repo: IPromotionTranslationRepository,
        event_bus: EventBus,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._translation_repo = translation_repo
        self._event_bus = event_bus

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
        locale: str = "en",
    ) -> PromotionOutput:
        source = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if source is None or source.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))

        # Discount-code promotions are 1:1 with coupons by domain rule;
        # there's no clean way to clone without forcing the merchant to
        # also create a fresh coupon first. Surface this clearly.
        if source.surface.value == "discount_code":
            raise CouponPromotionLinkError(
                "Cannot duplicate a discount_code promotion. "
                "Create a new coupon and a new promotion linked to it instead."
            )

        suffix = " (نسخة)" if locale == "ar" else " (copy)"
        new_name = f"{source.name}{suffix}"[:120]

        new_id = uuid4()
        clone = Promotion(
            id=new_id,
            tenant_id=tenant_id,
            store_id=store_id,
            name=new_name,
            surface=source.surface,
            status=PromotionStatus.DRAFT,
            coupon_id=None,
            discount_rule=source.discount_rule,
            content=source.content,
            translations=dict(source.translations),
            priority=source.priority,
            starts_at=None,
            ends_at=None,
            version=1,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )

        # Persist
        created = await self._promotion_repo.create(clone)

        src_displays = await self._display_repo.list_for_promotion(source.id)
        src_targets = await self._target_repo.list_for_promotion(source.id)

        cloned_displays = [
            PromotionDisplay(
                tenant_id=tenant_id,
                promotion_id=created.id,
                trigger=d.trigger,
                trigger_value=d.trigger_value,
                frequency=d.frequency,
                pages=d.pages,
                device_targets=d.device_targets,
                is_enabled=d.is_enabled,
            )
            for d in src_displays
        ]
        cloned_targets = [
            PromotionTarget(
                tenant_id=tenant_id,
                promotion_id=created.id,
                target_kind=t.target_kind,
                target_value=t.target_value,
                inclusion=t.inclusion,
            )
            for t in src_targets
        ]
        saved_displays = await self._display_repo.replace_for_promotion(
            created.id, cloned_displays
        )
        saved_targets = await self._target_repo.replace_for_promotion(
            created.id, cloned_targets
        )
        if source.translations:
            await self._translation_repo.replace_for_promotion(
                created.id, tenant_id, source.translations
            )

        self._event_bus.publish(
            PromotionCreatedEvent(
                promotion_id=created.id,
                store_id=store_id,
                tenant_id=tenant_id,
                surface=created.surface.value,
                actor_user_id=actor_user_id,
            )
        )

        return promotion_to_output(
            created, displays=saved_displays, targets=saved_targets
        )
