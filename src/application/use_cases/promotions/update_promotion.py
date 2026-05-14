"""UpdatePromotionUseCase — optimistic-locked partial update."""

from uuid import UUID

from src.application.dto.promotion import PromotionOutput, UpdatePromotionInput
from src.application.use_cases.promotions._mapping import promotion_to_output
from src.application.use_cases.promotions._validation import (
    validate_surface_payload,
)
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.events.base import EventBus
from src.core.events.promotion_events import PromotionUpdatedEvent
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    PromotionConflict,
    PromotionNotFound,
)
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.promotion_repository import (
    IPromotionDisplayRepository,
    IPromotionRepository,
    IPromotionTargetRepository,
    IPromotionTranslationRepository,
)


class UpdatePromotionUseCase:
    """Apply a partial update with optimistic locking."""

    def __init__(
        self,
        *,
        promotion_repo: IPromotionRepository,
        display_repo: IPromotionDisplayRepository,
        target_repo: IPromotionTargetRepository,
        translation_repo: IPromotionTranslationRepository,
        coupon_repo: ICouponRepository,
        event_bus: EventBus,
    ) -> None:
        self._promotion_repo = promotion_repo
        self._display_repo = display_repo
        self._target_repo = target_repo
        self._translation_repo = translation_repo
        self._coupon_repo = coupon_repo
        self._event_bus = event_bus

    async def execute(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        promotion_id: UUID,
        actor_user_id: UUID | None,
        payload: UpdatePromotionInput,
    ) -> PromotionOutput:
        current = await self._promotion_repo.get_by_id(store_id, promotion_id)
        if current is None or current.tenant_id != tenant_id:
            raise PromotionNotFound(str(promotion_id))

        if payload.version != current.version:
            raise PromotionConflict(current.version, payload.version)

        # Apply diffs onto a working copy.
        if payload.name is not None:
            current.name = payload.name
        if payload.priority is not None:
            current.priority = payload.priority
        if payload.starts_at is not None:
            current.starts_at = payload.starts_at
        if payload.ends_at is not None:
            current.ends_at = payload.ends_at
        if payload.coupon_id is not None:
            # When the caller sends coupon_id explicitly, validate it.
            coupon = await self._coupon_repo.get_by_id(payload.coupon_id)
            if coupon is None or coupon.store_id != store_id:
                raise CouponPromotionLinkError(
                    f"coupon {payload.coupon_id} not found in store {store_id}"
                )
            current.coupon_id = payload.coupon_id
        if payload.discount_rule is not None:
            current.discount_rule = payload.discount_rule
        if payload.content is not None:
            current.content = payload.content
        if payload.translations is not None:
            current.translations = payload.translations
        if payload.status is not None:
            current.status = payload.status
        # NOTE: follows the existing "non-null = patch" convention used by
        # the other fields on this use case. A merchant can RAISE the cap
        # or change the per-customer cap, but can't reset an existing cap
        # back to "uncapped" through this endpoint — same limitation as
        # `priority`, `coupon_id`, etc.
        if payload.usage_limit_total is not None:
            current.usage_limit_total = payload.usage_limit_total
        if payload.usage_limit_per_customer is not None:
            current.usage_limit_per_customer = payload.usage_limit_per_customer

        # Re-run the cross-field matrix on the resulting state.
        validate_surface_payload(
            current.surface,
            coupon_id_set=current.coupon_id is not None,
            discount_rule=current.discount_rule,
            content=current.content,
        )

        # Bump version (the repo enforces a WHERE version=:expected guard
        # for serialization safety; if it disagrees we re-raise).
        current.version += 1
        current.updated_by = actor_user_id
        current.touch()
        saved = await self._promotion_repo.update(current)

        # Replace child collections only when the caller sent them.
        if payload.displays is not None:
            displays = [
                PromotionDisplay(
                    tenant_id=tenant_id,
                    promotion_id=saved.id,
                    trigger=d.trigger,
                    trigger_value=d.trigger_value,
                    frequency=d.frequency,
                    pages=d.pages,
                    device_targets=d.device_targets,
                    is_enabled=d.is_enabled,
                )
                for d in payload.displays
            ]
            await self._display_repo.replace_for_promotion(saved.id, displays)
        if payload.targets is not None:
            targets = [
                PromotionTarget(
                    tenant_id=tenant_id,
                    promotion_id=saved.id,
                    target_kind=t.target_kind,
                    target_value=t.target_value,
                    inclusion=t.inclusion,
                    role=t.role,
                )
                for t in payload.targets
            ]
            await self._target_repo.replace_for_promotion(saved.id, targets)
        if payload.translations is not None:
            await self._translation_repo.replace_for_promotion(
                saved.id, tenant_id, payload.translations
            )

        displays_out = await self._display_repo.list_for_promotion(saved.id)
        targets_out = await self._target_repo.list_for_promotion(saved.id)

        self._event_bus.publish(
            PromotionUpdatedEvent(
                promotion_id=saved.id,
                store_id=store_id,
                tenant_id=tenant_id,
                surface=saved.surface.value,
                new_version=saved.version,
                actor_user_id=actor_user_id,
            )
        )

        return promotion_to_output(saved, displays=displays_out, targets=targets_out)
