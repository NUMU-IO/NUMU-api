"""Render a QR payload the customer can scan in their bank app.

The EMVCo Merchant-Presented QR standard that CBE officially advertises for
InstaPay is not yet published to merchants (as of 2026-04). In practice
every IPN-enabled bank app accepts a simple URI-style payload of the form

    instapay://pay?ipa=<ipa>&amount=<egp>&ref=<ref>&note=<note>

which we also expose as a plain human-readable string so any QR reader can
parse it even without deep-link support. When the official EMVCo profile
publishes, this module becomes the sole place to change the encoding —
callers only ever see ``qr_payload: str`` and ``qr_data_url: str``.
"""

from __future__ import annotations

import base64
import io
from urllib.parse import quote


def build_qr_payload(
    *,
    ipa: str,
    amount_cents: int,
    reference_code: str,
    note: str | None = None,
) -> str:
    """Compose the scannable payload string."""
    amount_egp = f"{amount_cents / 100:.2f}"
    parts = [
        f"ipa={quote(ipa, safe='@')}",
        f"amount={amount_egp}",
        f"ref={quote(reference_code, safe='')}",
    ]
    if note:
        parts.append(f"note={quote(note, safe='')}")
    return "instapay://pay?" + "&".join(parts)


def render_qr_data_url(payload: str) -> str:
    """Render an SVG-free PNG data URL.

    Using ``qrcode[pil]`` which is already a project dependency (see
    ``pyproject.toml``). Returns a ``data:image/png;base64,…`` string
    that the storefront can drop straight into an ``<img src>``.
    """
    import qrcode

    img = qrcode.make(payload)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
