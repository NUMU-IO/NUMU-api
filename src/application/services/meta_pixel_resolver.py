"""Wave 2 Phase 13 — Multi-pixel resolver.

Given a store's ``tracking.meta`` settings, returns the ordered list of
pixels every Meta CAPI fan-out should fire on. Backwards compatible:

  * If ``pixels[]`` is set in settings → use those entries.
  * Else fall back to the legacy single ``pixel_id`` field — emits a
    1-element list so callers see no behavior change.
  * Skips pixels with ``pixel_enabled=False`` (browser side) when called
    in browser-side mode, and ``capi_enabled=False`` when called in
    capi-side mode.

Plan: ``Plans/meta-pixels&CAPI/Meta-pixels&CAPI.md`` Phase 13.

**v1 design notes (deferred to v1.1 / Phase 13.2):**

  * Per-product pixel overrides (``product.meta_pixel_overrides``) are
    not yet plumbed — every product fans out to the store-level list.
  * Per-pixel CAPI credentials are not yet supported — all pixels under
    one store share the single credential row in ``service_credentials``.
    See PixelEntry docstring for the design rationale ("one Business
    Manager, one System User token, many pixels").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedPixel:
    """One pixel + its enablement flags as the resolver returns it."""

    pixel_id: str
    pixel_enabled: bool
    capi_enabled: bool
    label: str | None = None
    role: str | None = None


def resolve_pixels_for_product(
    store_meta_cfg: dict | None,
    product_overrides: dict | None,
    *,
    mode: str = "any",
) -> list[ResolvedPixel]:
    """Wave 2 Phase 13.2 — resolve pixels considering per-product overrides.

    ``product_overrides`` is the ``meta_pixel_overrides`` JSONB blob
    from the products table. Shape:
        {"override_mode": "exclusive"|"additive",
         "pixels": [{"pixel_id": str, "label": str?, "pixel_enabled": bool?, "capi_enabled": bool?}]}

    Behavior:
      * ``None`` overrides → falls through to ``resolve_pixels`` (no change).
      * ``override_mode="exclusive"`` → returns ONLY the override pixels;
        store-level pixels are skipped. Used when a media buyer dedicates
        a SKU to an agency-owned pixel and doesn't want the store-level
        pixel to also fire (avoids double-counting in dedup math).
      * ``override_mode="additive"`` → returns store-level pixels FIRST,
        then any override pixel_ids not already in that set (dedup by
        pixel_id). Used when the override is supplementary layering.
      * Defensive: malformed overrides (missing override_mode, empty
        pixels list, wrong types) fall through to store-level as if
        no override existed.
    """
    store_pixels = resolve_pixels(store_meta_cfg, mode=mode)
    if not isinstance(product_overrides, dict):
        return store_pixels

    raw_overrides = product_overrides.get("pixels")
    if not isinstance(raw_overrides, list) or not raw_overrides:
        return store_pixels

    override_mode = product_overrides.get("override_mode", "additive")
    if override_mode not in ("exclusive", "additive"):
        # Unknown mode → conservative fallthrough to store-level.
        return store_pixels

    override_entries: list[ResolvedPixel] = []
    for p in raw_overrides:
        if not isinstance(p, dict):
            continue
        pid = p.get("pixel_id")
        if not pid:
            continue
        pixel_enabled = bool(p.get("pixel_enabled", True))
        capi_enabled = bool(p.get("capi_enabled", True))
        if mode == "capi" and not capi_enabled:
            continue
        if mode == "pixel" and not pixel_enabled:
            continue
        override_entries.append(
            ResolvedPixel(
                pixel_id=str(pid),
                pixel_enabled=pixel_enabled,
                capi_enabled=capi_enabled,
                label=p.get("label"),
                role=p.get("role"),
            )
        )

    if not override_entries:
        return store_pixels

    if override_mode == "exclusive":
        return override_entries

    # additive — merge with dedup by pixel_id (store pixels keep priority).
    seen_ids = {e.pixel_id for e in store_pixels}
    merged = list(store_pixels)
    for entry in override_entries:
        if entry.pixel_id not in seen_ids:
            merged.append(entry)
            seen_ids.add(entry.pixel_id)
    return merged


def resolve_pixels(
    meta_cfg: dict | None,
    *,
    mode: str = "any",
) -> list[ResolvedPixel]:
    """Resolve the list of pixels to fire on.

    Args:
        meta_cfg: ``store.settings.tracking.meta`` dict (or None).
        mode: ``"any"`` (default) returns every configured pixel,
              ``"capi"`` filters to only ``capi_enabled=True`` pixels,
              ``"pixel"`` filters to only ``pixel_enabled=True``.

    Returns:
        Ordered list of ResolvedPixel. Empty when no pixels configured
        or when the filter excludes every entry.

    Backward-compat: when the new ``pixels[]`` array is absent but the
    legacy top-level ``pixel_id`` is set, returns a 1-element list
    constructed from the legacy fields so existing callers see no
    change in behavior.
    """
    if not meta_cfg:
        return []

    raw_pixels = meta_cfg.get("pixels")
    entries: list[ResolvedPixel] = []
    if isinstance(raw_pixels, list) and raw_pixels:
        for p in raw_pixels:
            if not isinstance(p, dict):
                continue
            pid = p.get("pixel_id")
            if not pid:
                continue
            entries.append(
                ResolvedPixel(
                    pixel_id=str(pid),
                    pixel_enabled=bool(p.get("pixel_enabled", True)),
                    capi_enabled=bool(p.get("capi_enabled", True)),
                    label=p.get("label"),
                    role=p.get("role"),
                )
            )
    else:
        # Legacy: single pixel from top-level fields. Preserves the
        # pre-Phase-13 contract so stores that never opt into multi-
        # pixel see zero behavior change.
        legacy_pid = meta_cfg.get("pixel_id")
        if legacy_pid:
            entries.append(
                ResolvedPixel(
                    pixel_id=str(legacy_pid),
                    pixel_enabled=bool(meta_cfg.get("pixel_enabled", False)),
                    capi_enabled=bool(meta_cfg.get("capi_enabled", False)),
                    label="Primary",
                    role="primary",
                )
            )

    if mode == "capi":
        return [e for e in entries if e.capi_enabled]
    if mode == "pixel":
        return [e for e in entries if e.pixel_enabled]
    return entries
