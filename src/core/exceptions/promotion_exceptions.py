"""Domain exceptions for the Offers / Promotions feature."""

from src.core.exceptions.base import DomainException


class PromotionException(DomainException):
    """Base class for all promotion-related domain errors."""


class PromotionNotFound(PromotionException):
    """Raised when a promotion cannot be located by id within a store."""

    def __init__(self, promotion_id: str | None = None) -> None:
        message = (
            f"Promotion '{promotion_id}' not found"
            if promotion_id
            else "Promotion not found"
        )
        super().__init__(message, code="PROMOTION_NOT_FOUND")


class PromotionStateError(PromotionException):
    """Raised on an invalid promotion lifecycle transition."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="PROMOTION_STATE_ERROR")


class PromotionConflict(PromotionException):
    """Raised when an optimistic-lock version mismatch occurs (HTTP 409)."""

    def __init__(self, current_version: int, attempted_version: int) -> None:
        self.current_version = current_version
        self.attempted_version = attempted_version
        message = (
            f"Promotion was modified by someone else "
            f"(current version {current_version}, you attempted {attempted_version}). "
            f"Refresh and try again."
        )
        super().__init__(message, code="PROMOTION_CONFLICT")


class InvalidDiscountRule(PromotionException):
    """Raised when a discount rule fails domain validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_DISCOUNT_RULE")


class CouponPromotionLinkError(PromotionException):
    """Raised when the coupon ↔ promotion link is invalid.

    Examples:
    * a `discount_code` promotion has no `coupon_id`
    * a non-discount-code promotion has a `coupon_id`
    * the linked coupon belongs to a different store
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="COUPON_PROMOTION_LINK_ERROR")
