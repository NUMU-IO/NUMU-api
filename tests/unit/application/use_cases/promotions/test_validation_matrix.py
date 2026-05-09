"""Surface ↔ payload validation matrix — table-driven test."""

import pytest

from src.application.use_cases.promotions._validation import (
    validate_surface_payload,
)
from src.core.enums.promotion_enums import PromotionSurface
from src.core.exceptions.promotion_exceptions import (
    CouponPromotionLinkError,
    InvalidDiscountRule,
)
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    CookieBannerContent,
    DiscountCodeContent,
    FloatingWidgetContent,
    PopupContent,
)

_PCT = DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10)


# Surface, coupon_id_set, discount_rule, content, expected_exc
_CASES = [
    # discount_code
    (PromotionSurface.DISCOUNT_CODE, True, None, DiscountCodeContent(), None),
    (
        PromotionSurface.DISCOUNT_CODE,
        False,
        None,
        DiscountCodeContent(),
        CouponPromotionLinkError,
    ),
    (
        PromotionSurface.DISCOUNT_CODE,
        True,
        _PCT,
        DiscountCodeContent(),
        InvalidDiscountRule,
    ),
    # automatic
    (PromotionSurface.AUTOMATIC, False, _PCT, AutomaticContent(), None),
    (PromotionSurface.AUTOMATIC, False, None, AutomaticContent(), InvalidDiscountRule),
    (
        PromotionSurface.AUTOMATIC,
        True,
        _PCT,
        AutomaticContent(),
        CouponPromotionLinkError,
    ),
    # announcement_bar
    (PromotionSurface.ANNOUNCEMENT_BAR, False, None, AnnouncementBarContent(), None),
    (PromotionSurface.ANNOUNCEMENT_BAR, False, _PCT, AnnouncementBarContent(), None),
    (
        PromotionSurface.ANNOUNCEMENT_BAR,
        True,
        None,
        AnnouncementBarContent(),
        CouponPromotionLinkError,
    ),
    (PromotionSurface.ANNOUNCEMENT_BAR, False, None, PopupContent(), ValueError),
    # popup
    (PromotionSurface.POPUP, False, None, PopupContent(), None),
    (PromotionSurface.POPUP, False, _PCT, PopupContent(), None),
    (PromotionSurface.POPUP, True, None, PopupContent(), CouponPromotionLinkError),
    # floating_widget
    (PromotionSurface.FLOATING_WIDGET, False, None, FloatingWidgetContent(), None),
    (PromotionSurface.FLOATING_WIDGET, False, _PCT, FloatingWidgetContent(), None),
    (
        PromotionSurface.FLOATING_WIDGET,
        True,
        None,
        FloatingWidgetContent(),
        CouponPromotionLinkError,
    ),
    # cookie_banner
    (PromotionSurface.COOKIE_BANNER, False, None, CookieBannerContent(), None),
    (
        PromotionSurface.COOKIE_BANNER,
        False,
        _PCT,
        CookieBannerContent(),
        InvalidDiscountRule,
    ),
    (
        PromotionSurface.COOKIE_BANNER,
        True,
        None,
        CookieBannerContent(),
        CouponPromotionLinkError,
    ),
]


@pytest.mark.parametrize(
    "surface, coupon_id_set, discount_rule, content, expected_exc",
    _CASES,
    ids=[
        f"{c[0].value}-coup={c[1]}-rule={'set' if c[2] else 'none'}-content={c[3].surface}"
        for c in _CASES
    ],
)
def test_matrix(surface, coupon_id_set, discount_rule, content, expected_exc):
    if expected_exc is None:
        validate_surface_payload(
            surface,
            coupon_id_set=coupon_id_set,
            discount_rule=discount_rule,
            content=content,
        )
    else:
        with pytest.raises(expected_exc):
            validate_surface_payload(
                surface,
                coupon_id_set=coupon_id_set,
                discount_rule=discount_rule,
                content=content,
            )
