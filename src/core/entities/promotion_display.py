"""PromotionDisplay entity — when/where/how a promotion is shown."""

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity
from src.core.enums.promotion_enums import DisplayFrequency, DisplayTrigger


class PromotionDisplay(BaseEntity):
    """One trigger-rule for a promotion.

    Multiple displays per promotion let the merchant target different
    audiences with different triggers without duplicating the discount
    rule itself. The `trigger_value` JSON is parsed by the service layer
    based on the `trigger` field — see `core/value_objects/trigger_value.py`.
    """

    tenant_id: UUID
    promotion_id: UUID
    trigger: DisplayTrigger
    trigger_value: dict[str, Any] = Field(default_factory=dict)
    frequency: DisplayFrequency
    pages: list[str] = Field(default_factory=list)
    device_targets: list[str] = Field(default_factory=lambda: ["desktop", "mobile"])
    is_enabled: bool = True

    def matches_page(self, page_path: str) -> bool:
        """True if the configured `pages` allowlist permits `page_path`.

        Empty list = all pages. Trailing `/*` is a one-segment wildcard.
        """
        if not self.pages:
            return True
        for pattern in self.pages:
            if pattern.endswith("/*"):
                prefix = pattern[:-2]
                if page_path == prefix or page_path.startswith(prefix + "/"):
                    return True
            elif pattern == page_path:
                return True
        return False

    def matches_device(self, device: str) -> bool:
        """True if `device` (e.g. 'mobile') is in `device_targets`."""
        return device in self.device_targets
