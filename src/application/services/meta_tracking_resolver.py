"""Meta tracking activation-mode resolver.

The dashboard surfaces a single radio-style "mode" picker
(``pixel_only`` / ``capi_only`` / ``both``), but the data model
persists **two independent booleans** — ``pixel_enabled`` and
``capi_enabled`` — so a merchant who toggles Pixel off and back on
doesn't lose their CAPI configuration (plan §3.2).

This single helper is consumed by both the API layer (to compute the
``mode`` in a settings GET response) and the storefront SSR (to know
whether to render the Pixel base script). Keeping the truth table in
one place prevents drift between frontend and backend.
"""

from typing import Literal

# Public type alias — exported so route schemas / tests can use it.
TrackingMode = Literal["off", "pixel_only", "capi_only", "both"]


def resolve_mode(meta_cfg: dict | None, has_capi_token: bool) -> TrackingMode:
    """Compute the active tracking mode from per-store config.

    Args:
        meta_cfg: The ``store.settings.tracking.meta`` JSON sub-object,
            or ``None`` / empty dict if the store has never configured
            Meta tracking.
        has_capi_token: Whether the store has an active, decryptable
            ``ServiceCredential`` row of type META_CAPI on file. The
            resolver does not pull credentials itself — keeping it
            pure-functional makes it trivially testable and safe to
            call from any layer (no I/O).

    Returns:
        One of "off", "pixel_only", "capi_only", "both".

    The activation gates are intentionally double-conditioned on
    ``pixel_id`` because a Pixel ID is required for *any* mode
    (CAPI POSTs go to ``/v21.0/{pixel_id}/events``). A merchant who
    flips ``capi_enabled = true`` without saving a Pixel ID first is
    treated as "off" — the settings PUT route should reject this with
    422 before it ever lands in the DB, but the resolver is the
    last line of defense.
    """
    cfg = meta_cfg or {}
    pixel_id_present = bool(cfg.get("pixel_id"))
    pixel_on = pixel_id_present and bool(cfg.get("pixel_enabled", False))
    capi_on = (
        pixel_id_present and bool(cfg.get("capi_enabled", False)) and has_capi_token
    )

    if pixel_on and capi_on:
        return "both"
    if pixel_on:
        return "pixel_only"
    if capi_on:
        return "capi_only"
    return "off"
