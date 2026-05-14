"""CreatePromotionUseCase — happy path + failure cases."""

import pytest

from src.application.dto.promotion import (
    CreatePromotionInput,
    PromotionDisplayInput,
    PromotionTargetInput,
)
from src.application.use_cases.promotions.create_promotion import (
    CreatePromotionUseCase,
)
from src.core.enums.promotion_enums import (
    DisplayFrequency,
    DisplayTrigger,
    PromotionStatus,
    PromotionSurface,
    TargetKind,
)
from src.core.exceptions import EntityNotFoundError
from src.core.exceptions.promotion_exceptions import CouponPromotionLinkError
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind
from src.core.value_objects.promotion_content import (
    AnnouncementBarContent,
    AutomaticContent,
    DiscountCodeContent,
    PopupContent,
)


def _build(**deps):
    return CreatePromotionUseCase(
        promotion_repo=deps["promotion_repo"],
        display_repo=deps["display_repo"],
        target_repo=deps["target_repo"],
        translation_repo=deps["translation_repo"],
        coupon_repo=deps["coupon_repo"],
        store_repo=deps["store_repo"],
        event_bus=deps["event_bus"],
    )


@pytest.mark.asyncio
async def test_create_announcement_bar_happy_path(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Free shipping bar",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        status=PromotionStatus.ACTIVE,
        content=AnnouncementBarContent(),
        displays=[
            PromotionDisplayInput(
                trigger=DisplayTrigger.ALWAYS,
                frequency=DisplayFrequency.UNTIL_DISMISSED,
            )
        ],
        targets=[
            PromotionTargetInput(
                target_kind=TargetKind.AUDIENCE,
                target_value={"kind": "all"},
            )
        ],
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.surface == PromotionSurface.ANNOUNCEMENT_BAR
    assert out.status == PromotionStatus.ACTIVE
    assert len(out.displays) == 1
    assert len(out.targets) == 1


@pytest.mark.asyncio
async def test_create_discount_code_requires_existing_coupon(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
    make_coupon,
):
    coupon = await make_coupon()
    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Welcome",
        surface=PromotionSurface.DISCOUNT_CODE,
        coupon_id=coupon.id,
        content=DiscountCodeContent(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.coupon_id == coupon.id


@pytest.mark.asyncio
async def test_create_discount_code_with_unknown_coupon_fails(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from uuid import uuid4

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Welcome",
        surface=PromotionSurface.DISCOUNT_CODE,
        coupon_id=uuid4(),
        content=DiscountCodeContent(),
    )
    with pytest.raises(CouponPromotionLinkError):
        await uc.execute(
            tenant_id=ids["tenant"],
            store_id=ids["store"],
            actor_user_id=ids["user"],
            payload=payload,
        )


@pytest.mark.asyncio
async def test_create_in_unknown_store_fails(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from uuid import uuid4

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Free shipping bar",
        surface=PromotionSurface.ANNOUNCEMENT_BAR,
        content=AnnouncementBarContent(),
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(
            tenant_id=ids["tenant"],
            store_id=uuid4(),
            actor_user_id=ids["user"],
            payload=payload,
        )


@pytest.mark.asyncio
async def test_active_in_future_flips_to_scheduled(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    from datetime import UTC, datetime, timedelta

    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Future auto",
        surface=PromotionSurface.AUTOMATIC,
        status=PromotionStatus.ACTIVE,
        starts_at=datetime.now(UTC) + timedelta(days=2),
        discount_rule=DiscountRule(kind=DiscountRuleKind.PERCENTAGE, value_percent=10),
        content=AutomaticContent(),
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.status == PromotionStatus.SCHEDULED


# ──────────────────────────────────────────────────────────────────────
# Regression: merchant-hub /marketing/promotions/new?surface=popup
# was returning 422 because (1) the input DTO didn't accept the
# usage_limit_* fields the form always sends, and (2) PopupContent
# didn't accept the collect_email/collect_phone booleans the form was
# wrapping the toggles in. The fix accepts usage_limit_* on the DTO
# and re-maps the booleans into the canonical `form_fields` list on
# the frontend boundary; PopupContent already had `form_fields`.
# ──────────────────────────────────────────────────────────────────────


def test_create_input_accepts_usage_limit_fields():
    """The merchant form always sends these; the DTO must accept them."""
    payload = CreatePromotionInput(
        name="Welcome popup",
        surface=PromotionSurface.POPUP,
        content=PopupContent(form_fields=["email"]),
        usage_limit_total=100,
        usage_limit_per_customer=1,
    )
    assert payload.usage_limit_total == 100
    assert payload.usage_limit_per_customer == 1


def test_create_input_usage_limits_default_to_none():
    payload = CreatePromotionInput(
        name="Uncapped",
        surface=PromotionSurface.POPUP,
        content=PopupContent(),
    )
    assert payload.usage_limit_total is None
    assert payload.usage_limit_per_customer is None


def test_create_input_rejects_non_positive_usage_limit():
    """usage_limit_total=0 makes no sense (a promo that can be used 0 times)."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CreatePromotionInput(
            name="Bad",
            surface=PromotionSurface.POPUP,
            content=PopupContent(),
            usage_limit_total=0,
        )


def test_popup_content_accepts_form_fields_list():
    """Storefront canonical shape — what the merchant-hub now sends."""
    p = PopupContent(form_fields=["email", "phone"])
    assert p.form_fields == ["email", "phone"]


def test_popup_content_rejects_legacy_collect_email_collect_phone():
    """The pre-fix merchant-hub shape — must stay rejected so we don't
    silently accept stale frontend payloads after a future regression."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        PopupContent(collect_email=True, collect_phone=True)


def test_create_input_accepts_popup_specific_translation_labels():
    """LocalizedPromotionContent has `extra='forbid'`. The popup form's
    email/phone/consent/submit/success labels caused 422 until these
    fields were declared. Lock the shape in."""
    payload = CreatePromotionInput(
        name="Welcome",
        surface=PromotionSurface.POPUP,
        content=PopupContent(form_fields=["email"]),
        translations={
            "en": {
                "headline": {"en": "Welcome!"},
                "email_label": {"en": "Email"},
                "phone_label": {"en": "Phone"},
                "consent_label": {"en": "I agree"},
                "submit_label": {"en": "Subscribe"},
                "success_headline": {"en": "Thanks!"},
                "success_body": {"en": "We'll be in touch"},
            },
        },
    )
    en = payload.translations["en"]
    assert en.email_label is not None
    assert en.success_headline is not None


def test_create_input_accepts_bogo_role_on_target():
    """BOGO buy_set/get_set role on a target was rejected as an extra
    field until exposed on the DTO."""
    from src.application.dto.promotion import PromotionTargetInput

    target = PromotionTargetInput(
        target_kind=TargetKind.PRODUCT,
        target_value={"product_ids": ["abc"]},
        inclusion=True,
        role="buy_set",
    )
    assert target.role == "buy_set"


def test_create_input_rejects_unknown_role_value():
    """Catch typos like role='buyset' — the field is a strict Literal."""
    import pydantic

    from src.application.dto.promotion import PromotionTargetInput

    with pytest.raises(pydantic.ValidationError):
        PromotionTargetInput(
            target_kind=TargetKind.PRODUCT,
            target_value={"product_ids": ["abc"]},
            role="buyset",  # missing underscore — invalid
        )


@pytest.mark.asyncio
async def test_create_popup_with_usage_limits_persists_them(
    ids,
    promotion_repo,
    display_repo,
    target_repo,
    translation_repo,
    coupon_repo,
    store_repo,
    event_bus,
):
    """End-to-end: the use case forwards usage_limit_* from the DTO
    onto the entity so they actually land on the persisted promotion."""
    uc = _build(
        promotion_repo=promotion_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        translation_repo=translation_repo,
        coupon_repo=coupon_repo,
        store_repo=store_repo,
        event_bus=event_bus,
    )
    payload = CreatePromotionInput(
        name="Welcome popup",
        surface=PromotionSurface.POPUP,
        content=PopupContent(form_fields=["email"]),
        usage_limit_total=50,
        usage_limit_per_customer=1,
    )
    out = await uc.execute(
        tenant_id=ids["tenant"],
        store_id=ids["store"],
        actor_user_id=ids["user"],
        payload=payload,
    )
    assert out.usage_limit_total == 50
    assert out.usage_limit_per_customer == 1
