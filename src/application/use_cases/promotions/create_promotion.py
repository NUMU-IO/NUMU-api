"""CreatePromotionUseCase."""

from datetime import UTC, datetime
from uuid import UUID

from src.application.dto.promotion import (
    CreatePromotionInput,
    PromotionOutput,
)
from src.application.use_cases.promotions._mapping import promotion_to_output
from src.application.use_cases.promotions._validation import (
    validate_surface_payload,
)
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import PromotionStatus
from src.core.events.base import EventBus
from src.core.events.promotion_events import PromotionCreatedEvent
from src.core.exceptions import EntityNotFoundError
from src.core.exceptions.promotion_exceptions import CouponPromotionLinkError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
    IPromotionTranslationRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class CreatePromotionUseCase:
    """Create a new promotion under a store."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        translation_repo: IPromotionTranslationRepository,
        coupon_repo: ICouponRepository,
        store_repo: IStoreRepository,
        event_bus: EventBus,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._translation_repo = translation_repo
        self._coupon_repo = coupon_repo
        self._store_repo = store_repo
        self._event_bus = event_bus

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        actor_user_id: UUID | None,
        payload: CreatePromotionInput,
    ) -> PromotionOutput:
        # 1. Verify store exists & belongs to tenant.
        store = await self._store_repo.get_by_id(store_id)
        if store is None or store.tenant_id != tenant_id:
            raise EntityNotFoundError("Store", str(store_id))

        # 2. Surface ↔ payload matrix.
        validate_surface_payload(
            payload.surface,
            coupon_id_set=payload.coupon_id is not None,
            discount_rule=payload.discount_rule,
            content=payload.content,
        )

        # 3. Coupon link verification.
        if payload.coupon_id is not None:
            coupon = await self._coupon_repo.get_by_id(payload.coupon_id)
            if coupon is None or coupon.store_id != store_id:
                raise CouponPromotionLinkError(
                    f"coupon {payload.coupon_id} not found in store {store_id}"
                )

        # 4. Auto-flip status: active in the future → scheduled.
        status = payload.status
        if (
            status == PromotionStatus.ACTIVE
            and payload.starts_at is not None
            and payload.starts_at > datetime.now(UTC)
        ):
            status = PromotionStatus.SCHEDULED

        # 5. Build the entity (validation auto-runs).
        promo = Promotion(
            tenant_id=tenant_id,
            store_id=store_id,
            name=payload.name,
            surface=payload.surface,
            status=status,
            coupon_id=payload.coupon_id,
            discount_rule=payload.discount_rule,
            content=payload.content,
            translations=payload.translations,
            priority=payload.priority,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )

        # 6. Persist atomically — promotion → displays → targets → translations.
        created = await self._promotion_repo.create(promo)
        displays = [
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
            for d in payload.displays
        ]
        targets = [
            PromotionTarget(
                tenant_id=tenant_id,
                promotion_id=created.id,
                target_kind=t.target_kind,
                target_value=t.target_value,
                inclusion=t.inclusion,
            )
            for t in payload.targets
        ]
        saved_displays = await self._display_repo.replace_for_promotion(
            created.id, displays
        )
        saved_targets = await self._target_repo.replace_for_promotion(
            created.id, targets
        )
        if payload.translations:
            await self._translation_repo.replace_for_promotion(
                created.id, tenant_id, payload.translations
            )

        # 7. Publish event so caches and analytics rollups invalidate.
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
            created,
            displays=saved_displays,
            targets=saved_targets,
        )
