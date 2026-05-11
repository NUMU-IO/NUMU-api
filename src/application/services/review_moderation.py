"""Review moderation policy resolver.

Decides whether a freshly-posted review is auto-approved, held for
manual review, or auto-approved-with-filter (visible only when free of
flagged language). Reads `store.settings.review_moderation` with the
following modes:

    "auto"               → publish immediately (default; matches the
                           pre-Phase-3 behavior)
    "manual"             → hold for merchant approval (is_approved=False)
    "auto_with_filter"   → publish only when neither title nor body
                           contains a flagged term; otherwise hold

Why this lives in `application/services` rather than the route:
    The same policy is reused by any path that creates reviews —
    storefront customer post, admin import, dashboard "post on
    customer's behalf". Keeping it in one place stops drift between
    those entry points (which was a real footgun: the storefront route
    auto-approved while the admin route held — opposite of what a
    typical merchant expects).

The profanity word list is intentionally minimal — Phase 3 ships a
bare-minimum filter that catches obvious slurs and lewd terms in
English + Arabic. Merchants who need stronger filtering should layer a
managed service (e.g. Perspective API) on top via a webhook in Phase 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Mode values stored in store.settings.review_moderation.
MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODE_AUTO_WITH_FILTER = "auto_with_filter"
DEFAULT_MODE = MODE_AUTO


# Tiny opt-in word list. We compile to a single regex with word
# boundaries so substring matches don't trigger ("classic" wouldn't
# match against an entry "ass"). The list is deliberately conservative;
# expansion happens via the merchant config rather than hardcoding.
_DEFAULT_FLAGGED_WORDS: tuple[str, ...] = (
    # English (truncated set; merchants extend via config)
    "fuck",
    "shit",
    "asshole",
    "bitch",
    "cunt",
    # Arabic (Egyptian colloquial slurs / common offensive terms)
    "حقير",
    "لعنة",
    "قحبة",
)


def _compile_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a case-insensitive whole-word matcher for the given list."""
    if not words:
        # Match-nothing fallback — ensures the calling code can rely on
        # `pattern.search` being safe regardless of config.
        return re.compile(r"$^")
    escaped = "|".join(re.escape(w) for w in words)
    # Arabic doesn't have ASCII word boundaries; we approximate by
    # bordering on whitespace or punctuation. ASCII-bounded words use
    # `\b` for proper "fuck" vs. "starstruck" disambiguation.
    return re.compile(rf"(?<!\w)({escaped})(?!\w)", re.IGNORECASE | re.UNICODE)


_DEFAULT_PATTERN = _compile_pattern(_DEFAULT_FLAGGED_WORDS)


@dataclass(frozen=True)
class ModerationDecision:
    """Output of `decide_review_visibility`."""

    is_approved: bool
    # When held (is_approved=False), explains why so the hub can show
    # merchants "held: contains flagged terms" vs "held: manual review".
    held_reason: str | None = None


def decide_review_visibility(
    *,
    store_settings: dict[str, Any] | None,
    title: str | None,
    body: str | None,
) -> ModerationDecision:
    """Apply the store's review-moderation policy to a new review.

    Args:
        store_settings: The store's `settings` JSONB. Pass {} or None
            to use the platform default (auto).
        title: Review title, may be None.
        body: Review body, may be None.

    Returns:
        ModerationDecision with `is_approved` flag plus an optional
        `held_reason` for the hub UI.
    """
    settings = store_settings or {}
    mode = (
        settings.get("review_moderation", {}).get("mode")
        if isinstance(settings.get("review_moderation"), dict)
        else settings.get("review_moderation")
    ) or DEFAULT_MODE

    if mode == MODE_MANUAL:
        return ModerationDecision(
            is_approved=False, held_reason="awaiting_manual_approval"
        )

    if mode == MODE_AUTO_WITH_FILTER:
        # Custom word list overlay — merchant-supplied additions extend
        # the platform default rather than replacing it (so the baseline
        # filter still catches obvious slurs even when the merchant
        # forgets to populate the list).
        custom_words: tuple[str, ...] = ()
        cfg = settings.get("review_moderation")
        if isinstance(cfg, dict):
            extras = cfg.get("flagged_words")
            if isinstance(extras, list):
                custom_words = tuple(str(w).strip() for w in extras if str(w).strip())
        pattern = (
            _compile_pattern(_DEFAULT_FLAGGED_WORDS + custom_words)
            if custom_words
            else _DEFAULT_PATTERN
        )

        haystack = " ".join(filter(None, [title, body])).strip()
        if haystack and pattern.search(haystack):
            return ModerationDecision(is_approved=False, held_reason="flagged_terms")
        return ModerationDecision(is_approved=True)

    # mode == MODE_AUTO (or unknown — fail open to maintain pre-Phase-3
    # behavior; a typo in the merchant config shouldn't drop reviews on
    # the floor).
    return ModerationDecision(is_approved=True)
