"""TikTok tracking activation-mode resolver — sibling of ``meta_tracking_resolver``.

The dashboard surfaces a single radio-style "mode" picker
(``pixel_only`` / ``api_only`` / ``both``), but the data model persists two
independent booleans — ``pixel_enabled`` and ``api_enabled`` — so a merchant
who toggles Pixel off and back on doesn't lose their Events API config.

Consumed by both the API layer (to compute ``mode`` in a settings GET) and
the storefront SSR (to know whether to render the Pixel base script).
Keeping the truth table in one place prevents frontend/backend drift.
"""

from typing import Literal

# Reuse the same mode vocabulary as Meta so the shared TrackingMode type and
# the hub's mode cards stay identical across providers.
TrackingMode = Literal["off", "pixel_only", "capi_only", "both"]


def resolve_tiktok_mode(tiktok_cfg: dict | None, has_api_token: bool) -> TrackingMode:
    """Compute the active TikTok tracking mode from per-store config.

    Args:
        tiktok_cfg: The ``store.settings.tracking.tiktok`` JSON sub-object,
            or ``None`` / empty dict if never configured.
        has_api_token: Whether the store has an active, decryptable
            ``ServiceCredential`` row of type TIKTOK_CAPI on file.

    Returns:
        One of "off", "pixel_only", "capi_only", "both". (``capi_only`` is
        reused verbatim for TikTok's Events-API-only mode so the shared
        ``TrackingMode`` literal and the hub UI cards don't fork.)

    Gates are double-conditioned on ``pixel_id`` because a Pixel Code is
    required for any mode (Events API posts carry ``event_source_id`` =
    pixel_id). A merchant who flips ``api_enabled`` without a pixel is
    treated as "off" — the PUT route rejects this with 422 first, but the
    resolver is the last line of defence.
    """
    cfg = tiktok_cfg or {}
    pixel_id_present = bool(cfg.get("pixel_id"))
    pixel_on = pixel_id_present and bool(cfg.get("pixel_enabled", False))
    api_on = pixel_id_present and bool(cfg.get("api_enabled", False)) and has_api_token

    if pixel_on and api_on:
        return "both"
    if pixel_on:
        return "pixel_only"
    if api_on:
        return "capi_only"
    return "off"
