"""Multi-pixel resolver for TikTok — sibling of ``meta_pixel_resolver``.

Given a store's ``tracking.tiktok`` settings, returns the ordered list of
pixels every Events API fan-out should fire on. Backwards compatible:

  * If ``pixels[]`` is set in settings → use those entries.
  * Else fall back to the legacy single ``pixel_id`` field — emits a
    1-element list so callers see no behaviour change.
  * ``mode="api"`` filters to ``api_enabled=True`` pixels; ``mode="pixel"``
    filters to ``pixel_enabled=True``; ``mode="any"`` returns all.

Naming delta vs Meta: TikTok's server-side toggle is ``api_enabled``
(TikTok calls the server rail "Events API", not "Conversions API").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedTikTokPixel:
    """One TikTok pixel + its enablement flags as the resolver returns it."""

    pixel_id: str
    pixel_enabled: bool
    api_enabled: bool
    label: str | None = None
    role: str | None = None


def resolve_tiktok_pixels(
    tiktok_cfg: dict | None,
    *,
    mode: str = "any",
) -> list[ResolvedTikTokPixel]:
    """Resolve the list of TikTok pixels to fire on.

    Args:
        tiktok_cfg: ``store.settings.tracking.tiktok`` dict (or None).
        mode: ``"any"`` returns every configured pixel, ``"api"`` filters
              to ``api_enabled=True`` only, ``"pixel"`` to
              ``pixel_enabled=True`` only.

    Returns:
        Ordered list of ResolvedTikTokPixel. Empty when no pixels are
        configured or when the filter excludes every entry.
    """
    if not tiktok_cfg:
        return []

    raw_pixels = tiktok_cfg.get("pixels")
    entries: list[ResolvedTikTokPixel] = []
    if isinstance(raw_pixels, list) and raw_pixels:
        for p in raw_pixels:
            if not isinstance(p, dict):
                continue
            pid = p.get("pixel_id")
            if not pid:
                continue
            entries.append(
                ResolvedTikTokPixel(
                    pixel_id=str(pid),
                    pixel_enabled=bool(p.get("pixel_enabled", True)),
                    api_enabled=bool(p.get("api_enabled", True)),
                    label=p.get("label"),
                    role=p.get("role"),
                )
            )
    else:
        legacy_pid = tiktok_cfg.get("pixel_id")
        if legacy_pid:
            entries.append(
                ResolvedTikTokPixel(
                    pixel_id=str(legacy_pid),
                    pixel_enabled=bool(tiktok_cfg.get("pixel_enabled", False)),
                    api_enabled=bool(tiktok_cfg.get("api_enabled", False)),
                    label="Primary",
                    role="primary",
                )
            )

    if mode == "api":
        return [e for e in entries if e.api_enabled]
    if mode == "pixel":
        return [e for e in entries if e.pixel_enabled]
    return entries
