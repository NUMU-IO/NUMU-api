"""Rewrite the links in a marketing email so clicks can be counted.

Every `href` in the rendered body is replaced with a URL on our own domain
carrying a per-recipient token and the link's position. Following it records
the click and redirects to the real destination.

Our own redirect, not the provider's. Resend can rewrite links for click
tracking, but it rewrites them to a Resend-owned host — the exact link/domain
mismatch its own deliverability report already flags against this account. A
metric is not worth spending sending reputation on when the alternative is one
route.

The destination list travels on the outreach row, not in the redirect's query
string. That is what keeps the endpoint from being an open redirect: it can
only send someone to a URL we wrote down at send time. A token in a query
string that named its own target would let anyone forge `numueg.app/...?u=`
into a phishing hop with our domain on the front of it.
"""

from __future__ import annotations

import re
import secrets

#: `href="..."` or `href='...'`. Deliberately not an HTML parser: the input is
#: our own template output plus operator copy, the pattern is anchored to the
#: attribute, and a parser here would be a dependency and a rewrite of markup
#: we already control.
#:
#: Anchored to `<a`, not to `href`, because href is not unique to links: the
#: shell carries `<link href=...>` for the webfonts and a first cut rewrote
#: that too. The consequence was not cosmetic — every client that loads
#: webfonts would have fetched the redirect and been counted as a click, so
#: "did they click" would have meant "did their mail client render this".
#: Caught by reading the link list of a real send, not by the preview.
_ANCHOR = re.compile(r"(<a\b[^>]*?\bhref=)([\"'])(.*?)\2", re.I | re.S)

#: Only these get rewritten. A `mailto:` or `tel:` link has no click to count
#: and would break if it were sent through an HTTP redirect.
_TRACKABLE = ("http://", "https://")

#: Links whose click means nothing, or must not gain a hop.
_NEVER_TRACK = ("/unsubscribe",)


def new_token() -> str:
    """A per-recipient token. 32 bytes: this is a public, guessable-by-URL
    lookup key, and a short one invites enumeration of who was mailed."""
    return secrets.token_urlsafe(32)


def rewrite_links(html: str, *, token: str, base_url: str) -> tuple[str, list[str]]:
    """Point every trackable href at the redirect.

    Returns the rewritten HTML and the destinations, positionally — index *i*
    in the list is what `/{token}/{i}` resolves to.

    The same destination appearing twice keeps one entry and both links point
    at it: a body with the referral URL in the text and again in the button is
    one destination, and counting it as two would make "which link did they
    click" a distinction without a difference.
    """
    links: list[str] = []
    base = base_url.rstrip("/")

    def replace(match: re.Match[str]) -> str:
        prefix, quote, url = match.group(1), match.group(2), match.group(3).strip()
        if not url.lower().startswith(_TRACKABLE):
            return match.group(0)
        if any(skip in url.lower() for skip in _NEVER_TRACK):
            # Unsubscribing is not engagement, and routing it through a
            # tracker adds a hop to the one link that must always work.
            return match.group(0)
        if url in links:
            index = links.index(url)
        else:
            links.append(url)
            index = len(links) - 1
        return f"{prefix}{quote}{base}/{token}/{index}{quote}"

    return _ANCHOR.sub(replace, html), links
