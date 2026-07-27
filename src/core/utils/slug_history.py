"""Slug rename history — how a renamed product/category keeps its ranking.

Renaming a catalogue row changes its storefront URL, and every indexed URL
and inbound link pointing at the old one 404s the moment the save lands.
Articles already solved this with ``previous_handles``; this is the same
rule for products and categories: the retired slug is remembered, the
storefront resolves it back to the current row, and the host answers with
a 301 to the canonical URL. A 301 transfers the accumulated ranking; a 404
discards it.

The list is CAPPED. Nothing prunes it — it is appended to on every rename
and read on every storefront slug miss — so leaving it unbounded is an
append-forever growth bug on a hot row. ``MAX_PREVIOUS_SLUGS`` keeps the
most RECENT entries: a slug retired ten renames ago has long since been
re-crawled through the chain of redirects, while the one retired yesterday
is the one still live in search results and in customers' saved links.
"""

MAX_PREVIOUS_SLUGS = 20


def append_slug_history(
    previous: list[str] | None,
    old_slug: str,
    new_slug: str,
) -> list[str]:
    """Slug history for a row being renamed ``old_slug`` → ``new_slug``.

    ``new_slug`` is dropped from the history if it is in there (a slug
    that is the CURRENT one must never also redirect — that is a redirect
    loop, and it happens whenever a merchant renames back to an earlier
    name). Entries stay unique and the newest ``MAX_PREVIOUS_SLUGS`` win.
    """
    history = [
        s for s in (previous or []) if isinstance(s, str) and s and s != new_slug
    ]
    if old_slug and old_slug != new_slug and old_slug not in history:
        history.append(old_slug)
    return history[-MAX_PREVIOUS_SLUGS:]
