"""Unit tests for the slug rename-history rule shared by products and
categories — the thing that turns a rename's 404 into a 301."""

from src.core.utils.slug_history import MAX_PREVIOUS_SLUGS, append_slug_history


class TestAppendSlugHistory:
    def test_first_rename_records_the_old_slug(self):
        assert append_slug_history([], "old-slug", "new-slug") == ["old-slug"]

    def test_none_history_is_treated_as_empty(self):
        assert append_slug_history(None, "old-slug", "new-slug") == ["old-slug"]

    def test_successive_renames_accumulate_oldest_first(self):
        history = append_slug_history([], "v1", "v2")
        history = append_slug_history(history, "v2", "v3")
        assert history == ["v1", "v2"]

    def test_rename_back_then_away_keeps_each_slug_once(self):
        # v1 → v2 → v1 → v3. Both retired slugs must redirect, and neither may
        # appear twice however many times the merchant flip-flops.
        history = append_slug_history([], "v1", "v2")
        history = append_slug_history(history, "v2", "v1")
        history = append_slug_history(history, "v1", "v3")
        assert history == ["v2", "v1"]
        assert len(history) == len(set(history))

    def test_retiring_a_slug_already_recorded_does_not_duplicate_it(self):
        assert append_slug_history(["v1", "v2"], "v2", "v3") == ["v1", "v2"]

    def test_current_slug_is_never_left_in_the_history(self):
        # Renaming BACK to an earlier slug must drop it from the history —
        # otherwise the current URL would redirect to itself.
        history = append_slug_history(["v1"], "v2", "v1")
        assert "v1" not in history
        assert history == ["v2"]

    def test_no_op_when_the_slug_did_not_change(self):
        assert append_slug_history(["v1"], "v2", "v2") == ["v1"]

    def test_blank_and_non_string_entries_are_dropped(self):
        # Defensive: the column is free-form JSONB, so a hand-edited or
        # imported row can carry junk that must not reach the storefront.
        history = append_slug_history(["", None, 7, "v1"], "v2", "v3")
        assert history == ["v1", "v2"]

    def test_history_is_capped_at_the_most_recent_entries(self):
        history: list[str] = []
        for i in range(MAX_PREVIOUS_SLUGS + 5):
            history = append_slug_history(history, f"slug-{i}", f"slug-{i + 1}")
        assert len(history) == MAX_PREVIOUS_SLUGS
        # The newest retired slug survives, the oldest is dropped.
        assert history[-1] == f"slug-{MAX_PREVIOUS_SLUGS + 4}"
        assert "slug-0" not in history
