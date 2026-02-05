"""Store onboarding entity tracking merchant setup progress."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class OnboardingStepKey(StrEnum):
    """Enumeration of all onboarding steps."""

    CREATE_STORE = "create_store"
    ADD_PRODUCT = "add_product"
    CONFIGURE_PAYMENT = "configure_payment"
    ADD_SHIPPING = "add_shipping"
    FIRST_ORDER = "first_order"


class OnboardingStepStatus(StrEnum):
    """Status of an individual onboarding step."""

    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


# Total number of steps -- used for percentage calculation
TOTAL_ONBOARDING_STEPS = len(OnboardingStepKey)

# Steps that cannot be skipped (already happened or required)
NON_SKIPPABLE_STEPS = frozenset({OnboardingStepKey.CREATE_STORE})


class StoreOnboarding(BaseEntity):
    """Tracks onboarding progress for a store.

    Each store gets one onboarding record. Steps are stored as a
    dict keyed by OnboardingStepKey with status/timestamp metadata.

    Steps dict format::

        {
            "create_store": {"status": "completed", "completed_at": "..."},
            "add_product": {"status": "pending"},
            "configure_payment": {"status": "skipped", "skipped_at": "..."},
            ...
        }
    """

    store_id: UUID
    steps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    is_completed: bool = False
    is_dismissed: bool = False
    completed_at: datetime | None = None
    dismissed_at: datetime | None = None

    def _ensure_steps_initialized(self) -> None:
        """Ensure all steps exist in the steps dict."""
        for key in OnboardingStepKey:
            if key.value not in self.steps:
                self.steps[key.value] = {
                    "status": OnboardingStepStatus.PENDING.value,
                }

    @property
    def completion_percentage(self) -> int:
        """Calculate completion percentage (0-100).

        Both 'completed' and 'skipped' steps count toward completion.
        """
        self._ensure_steps_initialized()
        done = sum(
            1
            for s in self.steps.values()
            if s.get("status")
            in (
                OnboardingStepStatus.COMPLETED.value,
                OnboardingStepStatus.SKIPPED.value,
            )
        )
        return int((done / TOTAL_ONBOARDING_STEPS) * 100)

    @property
    def current_step(self) -> str | None:
        """Return the first pending step key, or None if all done."""
        self._ensure_steps_initialized()
        for key in OnboardingStepKey:
            step_data = self.steps.get(key.value, {})
            if step_data.get("status") == OnboardingStepStatus.PENDING.value:
                return key.value
        return None

    def complete_step(self, step_key: OnboardingStepKey) -> bool:
        """Mark a step as completed. Returns True if state changed.

        Idempotent: completing an already-completed step is a no-op.
        """
        self._ensure_steps_initialized()
        step = self.steps.get(step_key.value, {})
        if step.get("status") == OnboardingStepStatus.COMPLETED.value:
            return False

        self.steps[step_key.value] = {
            "status": OnboardingStepStatus.COMPLETED.value,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        self._check_overall_completion()
        self.touch()
        return True

    def skip_step(self, step_key: OnboardingStepKey) -> None:
        """Mark a step as skipped.

        Raises:
            ValueError: If the step cannot be skipped.
        """
        if step_key in NON_SKIPPABLE_STEPS:
            raise ValueError(f"Step '{step_key.value}' cannot be skipped")

        self._ensure_steps_initialized()
        self.steps[step_key.value] = {
            "status": OnboardingStepStatus.SKIPPED.value,
            "skipped_at": datetime.now(UTC).isoformat(),
        }
        self._check_overall_completion()
        self.touch()

    def unskip_step(self, step_key: OnboardingStepKey) -> None:
        """Return a skipped step to pending."""
        self._ensure_steps_initialized()
        step = self.steps.get(step_key.value, {})
        if step.get("status") == OnboardingStepStatus.SKIPPED.value:
            self.steps[step_key.value] = {
                "status": OnboardingStepStatus.PENDING.value,
            }
            self.is_completed = False
            self.completed_at = None
            self.touch()

    def dismiss(self) -> None:
        """Dismiss the entire onboarding."""
        self.is_dismissed = True
        self.dismissed_at = datetime.now(UTC)
        self.touch()

    def _check_overall_completion(self) -> None:
        """Mark overall onboarding complete if all steps are done."""
        if self.completion_percentage == 100 and not self.is_completed:
            self.is_completed = True
            self.completed_at = datetime.now(UTC)
