"""Trigger-value value objects.

The parent `PromotionDisplay.trigger` enum decides which shape applies.
We don't use a Pydantic discriminator here because the discriminator
field lives on the parent, not inside the JSON payload.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.enums.promotion_enums import DisplayTrigger


class _BaseTriggerValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyTriggerValue(_BaseTriggerValue):
    """For triggers that need no extra config (`on_load`, `always`, …)."""


class OnDelayValue(_BaseTriggerValue):
    """`on_delay` — fire after N milliseconds on the page."""

    delay_ms: int = Field(ge=0, le=60_000)


class OnScrollPctValue(_BaseTriggerValue):
    """`on_scroll_pct` — fire after the visitor scrolls past X% of the page."""

    scroll_pct: int = Field(ge=1, le=100)


class OnExitIntentValue(_BaseTriggerValue):
    """`on_exit_intent` — fire on cursor-leave (desktop) / back-swipe (mobile)."""

    sensitivity: Literal["low", "medium", "high"] = "medium"


_TRIGGER_VALUE_BY_KIND: dict[DisplayTrigger, type[_BaseTriggerValue]] = {
    DisplayTrigger.ON_LOAD: EmptyTriggerValue,
    DisplayTrigger.ALWAYS: EmptyTriggerValue,
    DisplayTrigger.ON_ADD_TO_CART: EmptyTriggerValue,
    DisplayTrigger.ON_DELAY: OnDelayValue,
    DisplayTrigger.ON_SCROLL_PCT: OnScrollPctValue,
    DisplayTrigger.ON_EXIT_INTENT: OnExitIntentValue,
}


def parse_trigger_value(
    trigger: DisplayTrigger, raw: dict[str, Any] | None
) -> _BaseTriggerValue:
    """Parse a raw JSON payload into the right typed value object.

    Service-layer entry point: callers pass the trigger enum and the raw
    JSON dict (as stored in the DB) and get back a validated, typed VO.
    """
    cls = _TRIGGER_VALUE_BY_KIND[trigger]
    return cls.model_validate(raw or {})
