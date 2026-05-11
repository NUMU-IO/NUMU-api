"""Promotion aggregate root."""

from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from src.core.entities.base import BaseEntity
from src.core.enums.promotion_enums import PromotionStatus, PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    PromotionStateError,
)
from src.core.value_objects.discount_rule import DiscountRule
from src.core.value_objects.localized_promotion_content import (
    LocalizedPromotionContent,
)
from src.core.value_objects.promotion_content import PromotionContent


class Promotion(BaseEntity):
    """Top-level merchant-configured offer.

    Holds all the data the storefront needs to render the surface plus
    the bookkeeping the merchant needs to manage the lifecycle.
    """

    tenant_id: UUID
    store_id: UUID
    name: str = Field(min_length=1, max_length=120)
    surface: PromotionSurface
    status: PromotionStatus = PromotionStatus.DRAFT

    coupon_id: UUID | None = None
    discount_rule: DiscountRule | None = None
    content: PromotionContent
    translations: dict[str, LocalizedPromotionContent] = Field(default_factory=dict)

    priority: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    version: int = 1

    # Per-promotion usage caps. Both nullable; null = uncapped. The
    # eligibility checker stops the promotion once `convert` events
    # for this promotion meet the cap (total or per-customer). The
    # legacy `Coupon.usage_limit` only constrains code-based promos;
    # this pair covers automatic ones too (BOGO, tiered, percent-off
    # cart, etc.) where there's no coupon row.
    usage_limit_total: int | None = None
    usage_limit_per_customer: int | None = None

    created_by: UUID | None = None
    updated_by: UUID | None = None

    # ------------------------------------------------------------------ #
    # Invariants                                                          #
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def _enforce_invariants(self) -> Self:
        # Surface ↔ coupon link
        if self.surface == PromotionSurface.DISCOUNT_CODE and self.coupon_id is None:
            raise CouponPromotionLinkError("discount_code surface requires coupon_id")
        if (
            self.surface != PromotionSurface.DISCOUNT_CODE
            and self.coupon_id is not None
        ):
            raise CouponPromotionLinkError(
                "coupon_id only allowed when surface=discount_code"
            )
        # Schedule
        if (
            self.ends_at is not None
            and self.starts_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be strictly after starts_at")
        # Content surface must match the parent surface
        if self.content.surface != self.surface.value:
            raise ValueError(
                f"content.surface ({self.content.surface}) "
                f"must match promotion.surface ({self.surface.value})"
            )
        return self

    # ------------------------------------------------------------------ #
    # Lifecycle operations                                                #
    # ------------------------------------------------------------------ #

    def activate(self) -> None:
        """Move from draft / paused / scheduled to active."""
        allowed = {
            PromotionStatus.DRAFT,
            PromotionStatus.PAUSED,
            PromotionStatus.SCHEDULED,
        }
        if self.status not in allowed:
            raise PromotionStateError(
                f"cannot activate promotion in status {self.status}"
            )
        self.status = PromotionStatus.ACTIVE
        self.touch()

    def pause(self) -> None:
        """Pause an active or scheduled promotion."""
        allowed = {PromotionStatus.ACTIVE, PromotionStatus.SCHEDULED}
        if self.status not in allowed:
            raise PromotionStateError(f"cannot pause promotion in status {self.status}")
        self.status = PromotionStatus.PAUSED
        self.touch()

    def archive(self) -> None:
        """Archive a promotion. Terminal state."""
        if self.status == PromotionStatus.ARCHIVED:
            return
        self.status = PromotionStatus.ARCHIVED
        self.touch()

    def schedule(
        self,
        starts_at: datetime,
        ends_at: datetime | None = None,
    ) -> None:
        """Schedule a draft promotion to go active at `starts_at`."""
        if self.status not in {PromotionStatus.DRAFT, PromotionStatus.PAUSED}:
            raise PromotionStateError(
                f"cannot schedule promotion in status {self.status}"
            )
        if ends_at is not None and ends_at <= starts_at:
            raise ValueError("ends_at must be strictly after starts_at")
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.status = PromotionStatus.SCHEDULED
        self.touch()

    def expire_if_window_passed(self, *, now: datetime | None = None) -> bool:
        """If the active window has passed, mark expired. Returns True if changed."""
        moment = now or datetime.now(UTC)
        if (
            self.ends_at is not None
            and moment > self.ends_at
            and self.status
            in {
                PromotionStatus.ACTIVE,
                PromotionStatus.SCHEDULED,
                PromotionStatus.PAUSED,
            }
        ):
            self.status = PromotionStatus.EXPIRED
            self.touch()
            return True
        return False

    # ------------------------------------------------------------------ #
    # Computed                                                            #
    # ------------------------------------------------------------------ #

    @property
    def is_currently_active(self) -> bool:
        """True iff status==ACTIVE and now is within [starts_at, ends_at]."""
        if self.status != PromotionStatus.ACTIVE:
            return False
        now = datetime.now(UTC)
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now > self.ends_at:
            return False
        return True
