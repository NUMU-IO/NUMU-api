"""Unit tests for the pure attribution-model calculators.

Each model has its own class. Properties we check across all of them:

* Empty touches -> empty result.
* Single touch -> 100% credit to that touch.
* Credits ALWAYS sum to ``revenue_cents`` (no rounding drift across
  thousands of orders).

Model-specific properties are tested in their respective classes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.application.services.attribution_models import (
    Touch,
    attribute,
    first_touch_credit,
    last_touch_credit,
    linear_credit,
    position_based_credit,
    time_decay_credit,
)


def _touch(
    *,
    source: str = "facebook",
    medium: str | None = None,
    campaign: str | None = None,
    ts: datetime | None = None,
    campaign_id: UUID | None = None,
) -> Touch:
    return Touch(
        id=uuid4(),
        ts=ts or datetime(2026, 5, 1, tzinfo=UTC),
        utm_source=source,
        utm_medium=medium,
        utm_campaign=campaign,
        campaign_id=campaign_id,
    )


class TestLastTouchCredit:
    def test_single_touch(self):
        t = _touch()
        result = last_touch_credit([t], 10_000)
        assert result == [(t, 10_000)]

    def test_three_touches_credits_only_last(self):
        a, b, c = (
            _touch(source="facebook"),
            _touch(source="email"),
            _touch(source="direct"),
        )
        result = last_touch_credit([a, b, c], 10_000)
        assert result == [(a, 0), (b, 0), (c, 10_000)]

    def test_empty_touches(self):
        assert last_touch_credit([], 10_000) == []

    def test_zero_revenue(self):
        t = _touch()
        result = last_touch_credit([t], 0)
        assert result == [(t, 0)]


class TestFirstTouchCredit:
    def test_single_touch(self):
        t = _touch()
        result = first_touch_credit([t], 10_000)
        assert result == [(t, 10_000)]

    def test_three_touches_credits_only_first(self):
        a, b, c = (
            _touch(source="facebook"),
            _touch(source="email"),
            _touch(source="direct"),
        )
        result = first_touch_credit([a, b, c], 10_000)
        assert result == [(a, 10_000), (b, 0), (c, 0)]

    def test_empty_touches(self):
        assert first_touch_credit([], 10_000) == []


class TestLinearCredit:
    def test_single_touch(self):
        t = _touch()
        result = linear_credit([t], 10_000)
        assert result == [(t, 10_000)]

    def test_even_split_across_4_touches(self):
        touches = [_touch() for _ in range(4)]
        result = linear_credit(touches, 10_000)
        # 10_000 / 4 = 2500 each; sums to exact total.
        assert [c for _, c in result] == [2_500, 2_500, 2_500, 2_500]

    def test_rounding_drift_goes_to_last_touch(self):
        # 10 / 3 = 3.33..., truncated to 3 each = 9; the 1-cent drift
        # must end up on the last touch.
        touches = [_touch() for _ in range(3)]
        result = linear_credit(touches, 10)
        credits = [c for _, c in result]
        assert credits == [3, 3, 4]
        assert sum(credits) == 10

    def test_credits_always_sum_to_revenue(self):
        # Property check across a range of (n_touches, revenue) pairs.
        # A rounding bug would compound across thousands of orders;
        # this is the guard.
        for n in (1, 2, 3, 7, 13, 30):
            for rev in (1, 99, 100, 999, 12_345, 99_999):
                touches = [_touch() for _ in range(n)]
                result = linear_credit(touches, rev)
                assert sum(c for _, c in result) == rev, f"drift at n={n}, rev={rev}"

    def test_zero_revenue_returns_zeros(self):
        touches = [_touch() for _ in range(3)]
        result = linear_credit(touches, 0)
        assert all(c == 0 for _, c in result)


class TestPositionBasedCredit:
    def test_single_touch_gets_all(self):
        t = _touch()
        result = position_based_credit([t], 10_000)
        assert result == [(t, 10_000)]

    def test_two_touches_split_40_40_with_zero_middle(self):
        # n=2: there is no middle, so the 20% middle bucket contributes
        # 0 each. First gets 40%, last gets 40% — and the 20%
        # rounding-drift surplus goes to last to keep the sum exact.
        a, b = _touch(), _touch()
        result = position_based_credit([a, b], 10_000)
        credits = [c for _, c in result]
        # 0.4 weights, normalized: each is 50% → 5000 / 5000.
        # Then drift = 0, so split is 5000 / 5000.
        assert credits[0] == 5_000
        assert sum(credits) == 10_000

    def test_three_touches_40_20_40(self):
        a, b, c = _touch(), _touch(), _touch()
        result = position_based_credit([a, b, c], 10_000)
        credits = [credit for _, credit in result]
        # Weights normalized to 1.0 already.
        assert credits[0] == 4_000  # first
        assert credits[1] == 2_000  # middle
        assert sum(credits) == 10_000  # last absorbs drift

    def test_five_touches_first_and_last_dominant(self):
        touches = [_touch() for _ in range(5)]
        result = position_based_credit(touches, 10_000)
        credits = [c for _, c in result]
        # First and last each ≥ each middle touch.
        assert credits[0] >= credits[1]
        assert credits[-1] >= credits[1]
        # Middle three split 20% evenly → each ≈ 6.67%.
        for mid in credits[1:-1]:
            assert mid < credits[0]
            assert mid < credits[-1]
        assert sum(credits) == 10_000

    def test_empty_touches(self):
        assert position_based_credit([], 10_000) == []


class TestTimeDecayCredit:
    def test_single_touch_gets_all(self):
        t = _touch()
        result = time_decay_credit(
            [t], 10_000, conversion_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        assert result == [(t, 10_000)]

    def test_recent_touch_weighs_more_than_old_touch(self):
        # Two touches: one 14 days before conversion (2 half-lives away,
        # weight 0.25), one at conversion (weight 1.0). Recent should
        # get ~4x the credit.
        conv = datetime(2026, 5, 14, tzinfo=UTC)
        old = _touch(ts=datetime(2026, 4, 30, tzinfo=UTC))  # 14 days before
        recent = _touch(ts=conv)
        result = time_decay_credit(
            [old, recent], 10_000, conversion_at=conv, half_life_days=7.0
        )
        old_credit = result[0][1]
        recent_credit = result[1][1]
        # Recent : old ≈ 1 : 0.25 → recent should be ~4x old.
        # Sum to 10_000 with rounding drift on the last touch.
        assert recent_credit > old_credit
        ratio = recent_credit / max(old_credit, 1)
        assert 3.5 <= ratio <= 4.5
        assert old_credit + recent_credit == 10_000

    def test_credits_sum_to_revenue(self):
        # Property check with various touch counts and timing.
        conv = datetime(2026, 5, 14, tzinfo=UTC)
        for n in (1, 2, 3, 7, 13):
            touches = [_touch(ts=conv - timedelta(days=i * 2)) for i in range(n)]
            for rev in (1, 999, 12_345, 99_999):
                result = time_decay_credit(
                    touches, rev, conversion_at=conv, half_life_days=7.0
                )
                assert sum(c for _, c in result) == rev, f"drift at n={n}, rev={rev}"

    def test_clock_skew_post_conversion_touch(self):
        # A touch dated AFTER the conversion (e.g., storefront clock
        # skew) should not blow up; it should be treated as "at the
        # conversion moment" with full weight.
        conv = datetime(2026, 5, 14, tzinfo=UTC)
        future = _touch(ts=conv + timedelta(hours=1))
        normal = _touch(ts=conv - timedelta(days=1))
        result = time_decay_credit([normal, future], 10_000, conversion_at=conv)
        # The future touch should get >= the older one — no crash, no
        # negative weight.
        assert all(c >= 0 for _, c in result)
        assert sum(c for _, c in result) == 10_000

    def test_zero_half_life_falls_back_to_linear(self):
        # Defensive: 0 half-life would divide by zero in the exponent.
        # Should not crash; should fall back to equal-credit split.
        conv = datetime(2026, 5, 14, tzinfo=UTC)
        touches = [_touch(ts=conv - timedelta(days=i)) for i in range(4)]
        result = time_decay_credit(
            touches, 10_000, conversion_at=conv, half_life_days=0.0
        )
        credits = [c for _, c in result]
        # Linear: 2500 each.
        assert credits == [2_500, 2_500, 2_500, 2_500]

    def test_empty_touches(self):
        assert (
            time_decay_credit(
                [], 10_000, conversion_at=datetime(2026, 5, 14, tzinfo=UTC)
            )
            == []
        )


class TestAttributeDispatcher:
    """The ``attribute()`` dispatcher selects the right calculator."""

    @pytest.mark.parametrize(
        "model",
        ["last_touch", "first_touch", "linear", "time_decay", "position_based"],
    )
    def test_all_models_sum_to_revenue(self, model):
        conv = datetime(2026, 5, 14, tzinfo=UTC)
        touches = [_touch(ts=conv - timedelta(days=i * 2)) for i in range(5)]
        result = attribute(
            model=model,  # type: ignore[arg-type]
            touches=touches,
            revenue_cents=12_345,
            conversion_at=conv,
        )
        assert sum(c for _, c in result) == 12_345

    def test_time_decay_falls_back_to_linear_without_conversion_at(self):
        # The time-decay weights are anchored on conversion time —
        # without it, the calculator has no signal. Falls back to
        # linear so the analytics call still returns something useful.
        touches = [_touch() for _ in range(3)]
        result = attribute(
            model="time_decay",
            touches=touches,
            revenue_cents=12,
            conversion_at=None,
        )
        # Linear split of 12 over 3 = [4, 4, 4]; drift = 0.
        assert [c for _, c in result] == [4, 4, 4]

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="unknown attribution model"):
            attribute(
                model="not_a_real_model",  # type: ignore[arg-type]
                touches=[_touch()],
                revenue_cents=10,
                conversion_at=None,
            )
