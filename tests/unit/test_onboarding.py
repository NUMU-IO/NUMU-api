"""Unit tests for onboarding entity and auto-complete helpers.

Tests StoreOnboarding entity: step completion, skip, dismiss, percentage
calculation, current step tracking, and the auto-complete utility functions.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.entities.onboarding import (
    NON_SKIPPABLE_STEPS,
    TOTAL_ONBOARDING_STEPS,
    OnboardingStepKey,
    OnboardingStepStatus,
    StoreOnboarding,
)


class TestStoreOnboardingEntity:
    """Tests for the StoreOnboarding entity."""

    def _create_onboarding(self, **kwargs) -> StoreOnboarding:
        """Helper to create an onboarding entity."""
        defaults = {
            "id": uuid4(),
            "store_id": uuid4(),
        }
        defaults.update(kwargs)
        return StoreOnboarding(**defaults)

    def test_create_onboarding_defaults(self):
        """Test creating onboarding with default values."""
        onboarding = self._create_onboarding()

        assert onboarding.is_completed is False
        assert onboarding.is_dismissed is False
        assert onboarding.completed_at is None
        assert onboarding.dismissed_at is None
        assert onboarding.steps == {}

    def test_completion_percentage_all_pending(self):
        """Test percentage is 0 when all steps are pending."""
        onboarding = self._create_onboarding()

        assert onboarding.completion_percentage == 0

    def test_completion_percentage_one_completed(self):
        """Test percentage after completing one step."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        expected = int((1 / TOTAL_ONBOARDING_STEPS) * 100)
        assert onboarding.completion_percentage == expected

    def test_completion_percentage_all_completed(self):
        """Test percentage is 100 when all steps are completed."""
        onboarding = self._create_onboarding()
        for step in OnboardingStepKey:
            onboarding.complete_step(step)

        assert onboarding.completion_percentage == 100

    def test_completion_percentage_skipped_counts(self):
        """Test that skipped steps count toward completion."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
        onboarding.skip_step(OnboardingStepKey.ADD_PRODUCT)

        expected = int((2 / TOTAL_ONBOARDING_STEPS) * 100)
        assert onboarding.completion_percentage == expected

    def test_current_step_returns_first_pending(self):
        """Test current_step returns the first pending step."""
        onboarding = self._create_onboarding()

        # First pending step should be create_store (first in enum order)
        assert onboarding.current_step == OnboardingStepKey.CREATE_STORE.value

    def test_current_step_skips_completed(self):
        """Test current_step skips completed steps."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        assert onboarding.current_step == OnboardingStepKey.ADD_PRODUCT.value

    def test_current_step_none_when_all_done(self):
        """Test current_step is None when all steps are done."""
        onboarding = self._create_onboarding()
        for step in OnboardingStepKey:
            onboarding.complete_step(step)

        assert onboarding.current_step is None

    def test_current_step_skips_skipped_steps(self):
        """Test current_step skips both completed and skipped steps."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
        onboarding.skip_step(OnboardingStepKey.ADD_PRODUCT)

        assert onboarding.current_step == OnboardingStepKey.CONFIGURE_PAYMENT.value


class TestStoreOnboardingStepCompletion:
    """Tests for step completion logic."""

    def _create_onboarding(self, **kwargs) -> StoreOnboarding:
        defaults = {"id": uuid4(), "store_id": uuid4()}
        defaults.update(kwargs)
        return StoreOnboarding(**defaults)

    def test_complete_step_returns_true_on_change(self):
        """Test that completing a step returns True when state changes."""
        onboarding = self._create_onboarding()
        changed = onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        assert changed is True

    def test_complete_step_idempotent(self):
        """Test that completing an already-completed step returns False."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        changed = onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
        assert changed is False

    def test_complete_step_sets_timestamp(self):
        """Test that completing a step sets completed_at timestamp."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        step_data = onboarding.steps[OnboardingStepKey.CREATE_STORE.value]
        assert step_data["status"] == OnboardingStepStatus.COMPLETED.value
        assert "completed_at" in step_data

    def test_complete_step_updates_updated_at(self):
        """Test that completing a step calls touch() to update timestamps."""
        onboarding = self._create_onboarding()
        original_updated_at = onboarding.updated_at

        onboarding.complete_step(OnboardingStepKey.ADD_PRODUCT)
        # touch() should have been called
        assert onboarding.updated_at >= original_updated_at

    def test_complete_all_steps_marks_onboarding_complete(self):
        """Test that completing all steps marks overall onboarding as complete."""
        onboarding = self._create_onboarding()

        for step in OnboardingStepKey:
            onboarding.complete_step(step)

        assert onboarding.is_completed is True
        assert onboarding.completed_at is not None

    def test_partial_completion_does_not_mark_complete(self):
        """Test that partial completion doesn't mark onboarding as complete."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
        onboarding.complete_step(OnboardingStepKey.ADD_PRODUCT)

        assert onboarding.is_completed is False
        assert onboarding.completed_at is None

    def test_complete_step_can_override_skipped(self):
        """Test that completing a previously skipped step changes its status."""
        onboarding = self._create_onboarding()
        onboarding.skip_step(OnboardingStepKey.ADD_PRODUCT)

        # Now complete it
        changed = onboarding.complete_step(OnboardingStepKey.ADD_PRODUCT)
        assert changed is True

        step_data = onboarding.steps[OnboardingStepKey.ADD_PRODUCT.value]
        assert step_data["status"] == OnboardingStepStatus.COMPLETED.value


class TestStoreOnboardingSkipStep:
    """Tests for skip step logic."""

    def _create_onboarding(self, **kwargs) -> StoreOnboarding:
        defaults = {"id": uuid4(), "store_id": uuid4()}
        defaults.update(kwargs)
        return StoreOnboarding(**defaults)

    def test_skip_step_sets_status(self):
        """Test that skipping a step sets the skipped status."""
        onboarding = self._create_onboarding()
        onboarding.skip_step(OnboardingStepKey.ADD_PRODUCT)

        step_data = onboarding.steps[OnboardingStepKey.ADD_PRODUCT.value]
        assert step_data["status"] == OnboardingStepStatus.SKIPPED.value
        assert "skipped_at" in step_data

    def test_skip_non_skippable_step_raises(self):
        """Test that skipping a non-skippable step raises ValueError."""
        onboarding = self._create_onboarding()

        with pytest.raises(ValueError, match="cannot be skipped"):
            onboarding.skip_step(OnboardingStepKey.CREATE_STORE)

    def test_create_store_is_non_skippable(self):
        """Test that create_store is in the non-skippable set."""
        assert OnboardingStepKey.CREATE_STORE in NON_SKIPPABLE_STEPS

    def test_other_steps_are_skippable(self):
        """Test that other steps are not in the non-skippable set."""
        skippable_steps = set(OnboardingStepKey) - NON_SKIPPABLE_STEPS
        assert len(skippable_steps) == TOTAL_ONBOARDING_STEPS - len(NON_SKIPPABLE_STEPS)
        assert OnboardingStepKey.ADD_PRODUCT in skippable_steps
        assert OnboardingStepKey.CONFIGURE_PAYMENT in skippable_steps
        assert OnboardingStepKey.ADD_SHIPPING in skippable_steps
        assert OnboardingStepKey.FIRST_ORDER in skippable_steps

    def test_skip_all_skippable_plus_complete_required_marks_complete(self):
        """Test that skipping all skippable + completing required = 100%."""
        onboarding = self._create_onboarding()

        # Complete non-skippable
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        # Skip everything else
        for step in OnboardingStepKey:
            if step not in NON_SKIPPABLE_STEPS:
                onboarding.skip_step(step)

        assert onboarding.completion_percentage == 100
        assert onboarding.is_completed is True


class TestStoreOnboardingUnskipStep:
    """Tests for unskip step logic."""

    def _create_onboarding(self, **kwargs) -> StoreOnboarding:
        defaults = {"id": uuid4(), "store_id": uuid4()}
        defaults.update(kwargs)
        return StoreOnboarding(**defaults)

    def test_unskip_returns_step_to_pending(self):
        """Test that unskipping a step returns it to pending."""
        onboarding = self._create_onboarding()
        onboarding.skip_step(OnboardingStepKey.ADD_PRODUCT)
        onboarding.unskip_step(OnboardingStepKey.ADD_PRODUCT)

        step_data = onboarding.steps[OnboardingStepKey.ADD_PRODUCT.value]
        assert step_data["status"] == OnboardingStepStatus.PENDING.value

    def test_unskip_resets_overall_completion(self):
        """Test that unskipping a step resets overall completion."""
        onboarding = self._create_onboarding()

        # Complete everything
        for step in OnboardingStepKey:
            if step in NON_SKIPPABLE_STEPS:
                onboarding.complete_step(step)
            else:
                onboarding.skip_step(step)

        assert onboarding.is_completed is True

        # Unskip one step
        onboarding.unskip_step(OnboardingStepKey.ADD_PRODUCT)

        assert onboarding.is_completed is False
        assert onboarding.completed_at is None

    def test_unskip_non_skipped_step_is_noop(self):
        """Test that unskipping a pending step does nothing."""
        onboarding = self._create_onboarding()
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        # Try to unskip a pending step
        onboarding.unskip_step(OnboardingStepKey.ADD_PRODUCT)

        step_data = onboarding.steps[OnboardingStepKey.ADD_PRODUCT.value]
        assert step_data["status"] == OnboardingStepStatus.PENDING.value


class TestStoreOnboardingDismiss:
    """Tests for dismiss logic."""

    def _create_onboarding(self, **kwargs) -> StoreOnboarding:
        defaults = {"id": uuid4(), "store_id": uuid4()}
        defaults.update(kwargs)
        return StoreOnboarding(**defaults)

    def test_dismiss_sets_flags(self):
        """Test that dismissing sets is_dismissed and timestamp."""
        onboarding = self._create_onboarding()
        onboarding.dismiss()

        assert onboarding.is_dismissed is True
        assert onboarding.dismissed_at is not None

    def test_dismiss_is_idempotent(self):
        """Test that dismissing twice updates timestamp but doesn't break."""
        onboarding = self._create_onboarding()
        onboarding.dismiss()
        first_dismissed_at = onboarding.dismissed_at

        onboarding.dismiss()
        assert onboarding.is_dismissed is True
        # Second dismiss updates the timestamp
        assert onboarding.dismissed_at >= first_dismissed_at


class TestStoreOnboardingStepKeys:
    """Tests for onboarding step key enumeration."""

    def test_all_step_keys_exist(self):
        """Test that all expected step keys are defined."""
        expected_keys = {
            "create_store",
            "add_product",
            "configure_payment",
            "add_shipping",
            "first_order",
        }
        actual_keys = {step.value for step in OnboardingStepKey}
        assert actual_keys == expected_keys

    def test_total_steps_count(self):
        """Test that TOTAL_ONBOARDING_STEPS matches enum length."""
        assert TOTAL_ONBOARDING_STEPS == len(OnboardingStepKey)
        assert TOTAL_ONBOARDING_STEPS == 5


class TestAutoCompleteHelpers:
    """Tests for auto-complete utility functions."""

    @pytest.mark.asyncio
    async def test_try_complete_onboarding_step_success(self):
        """Test successful step completion via helper."""
        from src.application.use_cases.onboarding.auto_complete import (
            try_complete_onboarding_step,
        )

        store_id = uuid4()
        onboarding = StoreOnboarding(id=uuid4(), store_id=store_id)

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.return_value = onboarding
        mock_repo.update.return_value = onboarding

        await try_complete_onboarding_step(
            mock_repo, store_id, OnboardingStepKey.CREATE_STORE
        )

        mock_repo.get_by_store_id.assert_called_once_with(store_id)
        mock_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_try_complete_onboarding_step_no_record(self):
        """Test helper does nothing when no onboarding record exists."""
        from src.application.use_cases.onboarding.auto_complete import (
            try_complete_onboarding_step,
        )

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.return_value = None

        # Should not raise
        await try_complete_onboarding_step(
            mock_repo, uuid4(), OnboardingStepKey.ADD_PRODUCT
        )

        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_complete_onboarding_step_already_completed(self):
        """Test helper doesn't update when step is already completed."""
        from src.application.use_cases.onboarding.auto_complete import (
            try_complete_onboarding_step,
        )

        store_id = uuid4()
        onboarding = StoreOnboarding(id=uuid4(), store_id=store_id)
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.return_value = onboarding

        await try_complete_onboarding_step(
            mock_repo, store_id, OnboardingStepKey.CREATE_STORE
        )

        # complete_step returns False for already-completed, so no update
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_try_complete_onboarding_step_swallows_exceptions(self):
        """Test helper never raises exceptions."""
        from src.application.use_cases.onboarding.auto_complete import (
            try_complete_onboarding_step,
        )

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.side_effect = Exception("DB connection failed")

        # Should not raise
        await try_complete_onboarding_step(
            mock_repo, uuid4(), OnboardingStepKey.ADD_PRODUCT
        )

    @pytest.mark.asyncio
    async def test_init_onboarding_for_store_creates_record(self):
        """Test init_onboarding_for_store creates a new record."""
        from src.application.use_cases.onboarding.auto_complete import (
            init_onboarding_for_store,
        )

        store_id = uuid4()

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.return_value = None
        mock_repo.create.return_value = StoreOnboarding(id=uuid4(), store_id=store_id)

        await init_onboarding_for_store(mock_repo, store_id)

        mock_repo.get_by_store_id.assert_called_once_with(store_id)
        mock_repo.create.assert_called_once()

        # Verify the created entity has create_store completed
        created_entity = mock_repo.create.call_args[0][0]
        step_data = created_entity.steps.get(OnboardingStepKey.CREATE_STORE.value, {})
        assert step_data.get("status") == OnboardingStepStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_init_onboarding_for_store_idempotent(self):
        """Test init_onboarding_for_store is idempotent (doesn't create if exists)."""
        from src.application.use_cases.onboarding.auto_complete import (
            init_onboarding_for_store,
        )

        store_id = uuid4()
        existing = StoreOnboarding(id=uuid4(), store_id=store_id)

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.return_value = existing

        await init_onboarding_for_store(mock_repo, store_id)

        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_init_onboarding_for_store_swallows_exceptions(self):
        """Test init_onboarding_for_store never raises exceptions."""
        from src.application.use_cases.onboarding.auto_complete import (
            init_onboarding_for_store,
        )

        mock_repo = AsyncMock()
        mock_repo.get_by_store_id.side_effect = Exception("DB error")

        # Should not raise
        await init_onboarding_for_store(mock_repo, uuid4())
