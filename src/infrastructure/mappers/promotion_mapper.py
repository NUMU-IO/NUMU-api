"""Entity ↔ ORM converters for the Promotion aggregate.

Pure functions — no I/O, no session. Lifted into a class only because
the existing infra layer keeps a per-domain mapper class.
"""

from typing import Any
from uuid import uuid4

from src.core.entities.promotion import Promotion
from src.core.entities.promotion_dismissal import PromotionDismissal
from src.core.entities.promotion_display import PromotionDisplay
from src.core.entities.promotion_event import PromotionEvent
from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionEventType,
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.value_objects.discount_rule import DiscountRule
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    CookieBannerContent,
    DiscountCodeContent,
    FloatingWidgetContent,
    PopupContent,
)
from src.infrastructure.database.models.tenant.promotion import (
    PromotionDismissalModel,
    PromotionDisplayModel,
    PromotionEventModel,
    PromotionModel,
    PromotionTargetModel,
    PromotionTranslationModel,
)

_CONTENT_BY_SURFACE = {
    "discount_code": DiscountCodeContent,
    "automatic": AutomaticContent,
    "announcement_bar": AnnouncementBarContent,
    "popup": PopupContent,
    "floating_widget": FloatingWidgetContent,
    "cookie_banner": CookieBannerContent,
}


def _parse_content(surface: str, raw: dict[str, Any]) -> Any:
    """Pick the right Pydantic content class for a surface and validate."""
    cls = _CONTENT_BY_SURFACE[surface]
    payload = dict(raw or {})
    payload.setdefault("surface", surface)
    return cls.model_validate(payload)


class PromotionMapper:
    """Converts Promotion + child entities to/from SQLAlchemy models."""

    # ------------------------------------------------------------------ #
    # Promotion                                                           #
    # ------------------------------------------------------------------ #

    def promotion_to_orm(self, e: Promotion) -> PromotionModel:
        return PromotionModel(
            id=e.id,
            tenant_id=e.tenant_id,
            store_id=e.store_id,
            name=e.name,
            surface=e.surface.value,
            status=e.status.value,
            coupon_id=e.coupon_id,
            discount_rule=(
                e.discount_rule.model_dump(mode="json")
                if e.discount_rule is not None
                else None
            ),
            content=e.content.model_dump(mode="json"),
            priority=e.priority,
            starts_at=e.starts_at,
            ends_at=e.ends_at,
            version=e.version,
            usage_limit_total=e.usage_limit_total,
            usage_limit_per_customer=e.usage_limit_per_customer,
            created_by=e.created_by,
            updated_by=e.updated_by,
        )

    def promotion_to_entity(
        self,
        m: PromotionModel,
        *,
        translations: dict[str, LocalizedPromotionContent] | None = None,
    ) -> Promotion:
        return Promotion(
            id=m.id,
            tenant_id=m.tenant_id,
            store_id=m.store_id,
            name=m.name,
            surface=PromotionSurface(m.surface),
            status=PromotionStatus(m.status),
            coupon_id=m.coupon_id,
            discount_rule=(
                DiscountRule.model_validate(m.discount_rule)
                if m.discount_rule is not None
                else None
            ),
            content=_parse_content(m.surface, m.content or {}),
            translations=translations or {},
            priority=m.priority,
            starts_at=m.starts_at,
            ends_at=m.ends_at,
            version=m.version,
            usage_limit_total=m.usage_limit_total,
            usage_limit_per_customer=m.usage_limit_per_customer,
            created_by=m.created_by,
            updated_by=m.updated_by,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    # ------------------------------------------------------------------ #
    # Display                                                             #
    # ------------------------------------------------------------------ #

    def display_to_orm(self, d: PromotionDisplay) -> PromotionDisplayModel:
        return PromotionDisplayModel(
            id=d.id,
            tenant_id=d.tenant_id,
            promotion_id=d.promotion_id,
            trigger=d.trigger.value,
            trigger_value=d.trigger_value,
            frequency=d.frequency.value,
            pages=d.pages,
            device_targets=d.device_targets,
            is_enabled=d.is_enabled,
        )

    def display_to_entity(self, m: PromotionDisplayModel) -> PromotionDisplay:
        return PromotionDisplay(
            id=m.id,
            tenant_id=m.tenant_id,
            promotion_id=m.promotion_id,
            trigger=DisplayTrigger(m.trigger),
            trigger_value=m.trigger_value or {},
            frequency=DisplayFrequency(m.frequency),
            pages=list(m.pages or []),
            device_targets=list(m.device_targets or []),
            is_enabled=m.is_enabled,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    # ------------------------------------------------------------------ #
    # Target                                                              #
    # ------------------------------------------------------------------ #

    def target_to_orm(self, t: PromotionTarget) -> PromotionTargetModel:
        return PromotionTargetModel(
            id=t.id,
            tenant_id=t.tenant_id,
            promotion_id=t.promotion_id,
            target_kind=t.target_kind.value,
            target_value=t.target_value,
            inclusion=t.inclusion,
            role=t.role,
        )

    def target_to_entity(self, m: PromotionTargetModel) -> PromotionTarget:
        # `role` is `"buy_set" | "get_set" | None`. The entity uses
        # `Literal[...]` so cast through `Any` rather than tighten the
        # column type with a Postgres enum — that lets us add roles
        # later without a migration.
        role = m.role if m.role in ("buy_set", "get_set") else None
        return PromotionTarget(
            id=m.id,
            tenant_id=m.tenant_id,
            promotion_id=m.promotion_id,
            target_kind=TargetKind(m.target_kind),
            target_value=m.target_value or {},
            inclusion=m.inclusion,
            role=role,  # type: ignore[arg-type]
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    # ------------------------------------------------------------------ #
    # Translation                                                         #
    # ------------------------------------------------------------------ #

    def translation_to_orm(
        self,
        tenant_id,
        promotion_id,
        locale: str,
        content: LocalizedPromotionContent,
    ) -> PromotionTranslationModel:
        return PromotionTranslationModel(
            id=uuid4(),
            tenant_id=tenant_id,
            promotion_id=promotion_id,
            locale=locale,
            content=content.model_dump(mode="json", exclude_none=True),
        )

    def translations_to_dict(
        self, models: list[PromotionTranslationModel]
    ) -> dict[str, LocalizedPromotionContent]:
        return {
            m.locale: LocalizedPromotionContent.model_validate(m.content or {})
            for m in models
        }

    # ------------------------------------------------------------------ #
    # Event                                                               #
    # ------------------------------------------------------------------ #

    def event_to_orm(self, e: PromotionEvent) -> PromotionEventModel:
        return PromotionEventModel(
            id=e.id,
            tenant_id=e.tenant_id,
            store_id=e.store_id,
            promotion_id=e.promotion_id,
            event_type=e.event_type.value,
            customer_id=e.customer_id,
            session_id=e.session_id,
            order_id=e.order_id,
            discount_amount_cents=e.discount_amount_cents,
            event_metadata=e.metadata,
            occurred_at=e.occurred_at,
        )

    def event_to_entity(self, m: PromotionEventModel) -> PromotionEvent:
        return PromotionEvent(
            id=m.id,
            tenant_id=m.tenant_id,
            store_id=m.store_id,
            promotion_id=m.promotion_id,
            event_type=PromotionEventType(m.event_type),
            customer_id=m.customer_id,
            session_id=m.session_id,
            order_id=m.order_id,
            discount_amount_cents=m.discount_amount_cents,
            metadata=m.event_metadata or {},
            occurred_at=m.occurred_at,
        )

    # ------------------------------------------------------------------ #
    # Dismissal                                                           #
    # ------------------------------------------------------------------ #

    def dismissal_to_orm(self, d: PromotionDismissal) -> PromotionDismissalModel:
        return PromotionDismissalModel(
            id=d.id,
            tenant_id=d.tenant_id,
            promotion_id=d.promotion_id,
            customer_id=d.customer_id,
            visitor_token=d.visitor_token,
            dismissed_at=d.dismissed_at,
        )
