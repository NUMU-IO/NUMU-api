"""Internal mappers entity ↔ DTO for the promotions use cases.

Keeps boilerplate out of every use case file. Pure functions — no I/O.
"""

from src.application.dto.promotion import (
    PromotionDisplayOutput,
    PromotionListItemOutput,
    PromotionMetricsBlock,
    PromotionOutput,
    PromotionTargetOutput,
)
from src.core.entities.promotion import Promotion
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_target import PromotionTarget
from src.core.value_objects.promotion_content import PromotionContent


def display_to_output(d: PromotionDisplay) -> PromotionDisplayOutput:
    return PromotionDisplayOutput(
        id=d.id,
        trigger=d.trigger,
        trigger_value=d.trigger_value,
        frequency=d.frequency,
        pages=d.pages,
        device_targets=d.device_targets,
        is_enabled=d.is_enabled,
    )


def target_to_output(t: PromotionTarget) -> PromotionTargetOutput:
    return PromotionTargetOutput(
        id=t.id,
        target_kind=t.target_kind,
        target_value=t.target_value,
        inclusion=t.inclusion,
        role=t.role,
    )


def promotion_to_output(
    promo: Promotion,
    *,
    displays: list[PromotionDisplay],
    targets: list[PromotionTarget],
    metrics: PromotionMetricsBlock | None = None,
) -> PromotionOutput:
    """Full read model. `metrics` is a no-op zeroed block if not provided."""
    content_payload: PromotionContent = promo.content
    return PromotionOutput(
        id=promo.id,
        tenant_id=promo.tenant_id,
        store_id=promo.store_id,
        name=promo.name,
        surface=promo.surface,
        status=promo.status,
        coupon_id=promo.coupon_id,
        discount_rule=promo.discount_rule,
        content=content_payload.model_dump(),
        translations=promo.translations,
        displays=[display_to_output(d) for d in displays],
        targets=[target_to_output(t) for t in targets],
        priority=promo.priority,
        starts_at=promo.starts_at,
        ends_at=promo.ends_at,
        version=promo.version,
        created_at=promo.created_at,
        updated_at=promo.updated_at,
        usage_limit_total=promo.usage_limit_total,
        usage_limit_per_customer=promo.usage_limit_per_customer,
        metrics=metrics or PromotionMetricsBlock(),
    )


def promotion_to_list_item(promo: Promotion) -> PromotionListItemOutput:
    return PromotionListItemOutput(
        id=promo.id,
        name=promo.name,
        surface=promo.surface,
        status=promo.status,
        priority=promo.priority,
        starts_at=promo.starts_at,
        ends_at=promo.ends_at,
        version=promo.version,
        coupon_id=promo.coupon_id,
        created_at=promo.created_at,
        updated_at=promo.updated_at,
    )
