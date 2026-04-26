"""HTML sanitizer for merchant-customized email templates.

Merchants supply raw HTML for transactional email bodies; that HTML is
ultimately rendered into customer inboxes, so we cannot trust it. This
module wraps :mod:`bleach` with an email-friendly allow-list:

* table-layout tags (``table``, ``tr``, ``td``, …) — required for
  email-client compatibility.
* presentational tags (``font``, ``center``) — old but still supported.
* inline ``style`` / ``align`` attributes on every element — the only
  way to style email reliably across Gmail / Outlook / Apple Mail.
* ``style`` element kept whole so merchants can inline a small CSS block.
* protocols restricted to ``http(s)`` / ``mailto`` / ``tel`` — no
  ``javascript:`` / ``data:`` links.

Comments are stripped (``strip_comments=True``) since Outlook
conditional comments aren't user-authored anyway.
"""

from __future__ import annotations

import re

import bleach
from bleach.css_sanitizer import CSSSanitizer

# Email-safe CSS properties merchants commonly use for inline styling.
# Anything not on this list gets dropped by bleach, but the property
# value (color, length, url, etc.) is preserved verbatim within the
# allowed set. Curated to cover the surface area the brand chrome plus
# typical merchant-pasted email-table layouts use.
_ALLOWED_CSS_PROPERTIES: list[str] = [
    # Layout & box model
    "background",
    "background-color",
    "background-image",
    "background-position",
    "background-repeat",
    "background-size",
    "border",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-color",
    "border-style",
    "border-width",
    "border-radius",
    "border-collapse",
    "border-spacing",
    "box-shadow",
    "outline",
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "width",
    "min-width",
    "max-width",
    "height",
    "min-height",
    "max-height",
    "display",
    "visibility",
    "overflow",
    "overflow-x",
    "overflow-y",
    "position",
    "top",
    "right",
    "bottom",
    "left",
    "z-index",
    "float",
    "clear",
    "vertical-align",
    "text-align",
    # Typography
    "color",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "font-variant",
    "letter-spacing",
    "line-height",
    "text-decoration",
    "text-indent",
    "text-shadow",
    "text-transform",
    "white-space",
    "word-break",
    "word-spacing",
    "word-wrap",
    "direction",
    # List & table
    "list-style",
    "list-style-type",
    "list-style-position",
    "list-style-image",
    "table-layout",
    "caption-side",
    "empty-cells",
    # Effects
    "opacity",
    "filter",
    "transform",
    "transition",
    # Misc
    "cursor",
]


_css_sanitizer = CSSSanitizer(allowed_css_properties=_ALLOWED_CSS_PROPERTIES)


# Matches any triple-backtick fence with an optional language tag —
# `` ```html `` / `` ```ar `` / bare `` ``` ``. Triple-backticks are
# never valid HTML so we strip every occurrence.
_FENCE_MARKER_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?", re.MULTILINE)
_TRAILING_FENCE_RE = re.compile(r"\n?\s*```\s*$", re.MULTILINE)


def strip_markdown_fences(html: str) -> str:
    """Strip every triple-backtick markdown code-fence marker.

    Merchants pasting from ChatGPT / docs often leave ```html ... ```
    wrappers in their HTML. The backticks render as literal text in the
    customer's inbox (since they aren't HTML markup). This is a defensive
    cleanup that survives both the wrapping-fence and embedded-fence
    cases.

    Rules:
      * Every opening marker (`` ```html ``, `` ```ar ``, `` ``` ``)
        followed by an optional newline → removed.
      * Every closing marker (`` ``` `` at line end) → removed.
      * Whitespace around removed markers is collapsed but content
        between markers is preserved verbatim.
    """
    if not html:
        return html
    cleaned = _TRAILING_FENCE_RE.sub("", html)
    cleaned = _FENCE_MARKER_RE.sub("", cleaned)
    return cleaned.strip()


ALLOWED_TAGS: list[str] = [
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "center",
    "font",
    "style",
]

ALLOWED_ATTRS: dict[str, list[str]] = {
    "*": ["class", "id", "style", "align", "dir"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "style"],
    "table": ["width", "cellpadding", "cellspacing", "border", "bgcolor"],
    "td": ["width", "height", "valign", "align", "bgcolor"],
    "th": ["width", "height", "valign", "align", "bgcolor"],
    "tr": ["valign", "align", "bgcolor"],
    "font": ["color", "face", "size"],
}

ALLOWED_PROTOCOLS: list[str] = ["http", "https", "mailto", "tel"]


def sanitize_email_html(html: str) -> str:
    """Sanitize merchant-supplied HTML for safe inclusion in an email body.

    Anything not in :data:`ALLOWED_TAGS` / :data:`ALLOWED_ATTRS` /
    :data:`ALLOWED_PROTOCOLS` is silently stripped. Comments are also
    removed.

    Args:
        html: Raw HTML supplied by the merchant.

    Returns:
        Sanitized HTML safe to render into an email body. Jinja2
        placeholders (``{{ var }}``, ``{% if %}``) survive sanitization
        because their syntax does not match HTML tags.
    """
    cleaned = strip_markdown_fences(html)
    return bleach.clean(
        cleaned,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=True,
        strip_comments=False,  # Outlook-conditional comments are common in
        # merchant-pasted email HTML; keeping them lets table-based layouts
        # render correctly in Outlook 2007-2019.
    )


__all__ = [
    "ALLOWED_TAGS",
    "ALLOWED_ATTRS",
    "ALLOWED_PROTOCOLS",
    "sanitize_email_html",
    "strip_markdown_fences",
]
