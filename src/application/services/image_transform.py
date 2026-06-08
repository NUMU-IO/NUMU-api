"""Non-destructive image-transform sanitization for V3 theme customization.

Image setting values may carry optional focal/zoom/rotation METADATA
(``{ "url": ..., "alt": ..., "transform": {...} }``) that the storefront
reproduces purely via CSS — the original asset is never modified. The
customization payload is free-form JSON validated only once at the Pydantic
boundary, so a buggy or hostile client could store an out-of-range transform
(``zoom: 9999``, ``focal.x: -5``) that the renderer would have to defend
against. The storefront + editor already CLAMP at render time, so this is
defense-in-depth: it keeps the persisted data clean and bounded.

``clamp_transform`` is intentionally LENIENT (clamp what's valid, drop garbage)
rather than strict-reject, because it runs over opaque free-form JSON on every
autosave and must never reject an otherwise-valid customization. The
per-asset-default path (asset_meta) uses strict Pydantic validation instead.
"""

from __future__ import annotations

import copy
import math
from typing import Any

_FITS = {"cover", "contain"}


def _finite(v: Any) -> float:
    """float(v) that rejects NaN/Infinity (which would corrupt the JSON)."""
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("non-finite")
    return f


def clamp_transform(t: Any) -> dict[str, Any] | None:
    """Clamp a single transform dict to safe ranges.

    Returns a cleaned dict, or ``None`` when nothing usable survives (the
    caller then drops the ``transform`` key entirely → identity render).
    Each sub-field is validated independently: a malformed field is dropped,
    the rest is kept.
    """
    if not isinstance(t, dict):
        return None

    out: dict[str, Any] = {"v": 1}

    focal = t.get("focal")
    if isinstance(focal, dict):
        try:
            out["focal"] = {
                "x": min(1.0, max(0.0, _finite(focal.get("x")))),
                "y": min(1.0, max(0.0, _finite(focal.get("y")))),
            }
        except (TypeError, ValueError):
            pass  # drop malformed focal, keep the rest

    zoom = t.get("zoom")
    if zoom is not None:
        try:
            out["zoom"] = min(4.0, max(1.0, _finite(zoom)))
        except (TypeError, ValueError):
            pass

    rotation = t.get("rotation")
    if rotation is not None:
        try:
            out["rotation"] = int(_finite(rotation)) % 360
        except (TypeError, ValueError):
            pass

    fit = t.get("fit")
    if fit in _FITS:
        out["fit"] = fit

    # Only the version tag survived → nothing meaningful; treat as identity.
    if len(out) == 1:
        return None
    return out


def sanitize_transform_settings(v3_dict: Any) -> Any:
    """Return a copy of a V3 customization with every image ``transform`` clamped.

    Walks templates → sections → settings, section blocks (recursively),
    section_groups, and global_settings. Any setting value that is a dict
    carrying a ``transform`` key is clamped via :func:`clamp_transform`; an
    unusable transform is removed. All other data is preserved untouched.
    """
    if not isinstance(v3_dict, dict):
        return v3_dict

    result = copy.deepcopy(v3_dict)

    def walk_settings(settings: Any) -> None:
        if not isinstance(settings, dict):
            return
        for value in settings.values():
            if isinstance(value, dict) and "transform" in value:
                cleaned = clamp_transform(value.get("transform"))
                if cleaned is None:
                    value.pop("transform", None)
                else:
                    value["transform"] = cleaned

    def walk_block(block: Any) -> None:
        if not isinstance(block, dict):
            return
        walk_settings(block.get("settings"))
        nested = block.get("blocks")
        if isinstance(nested, dict):
            for child in nested.values():
                walk_block(child)

    def walk_sections(sections: Any) -> None:
        if not isinstance(sections, dict):
            return
        for sec in sections.values():
            if not isinstance(sec, dict):
                continue
            walk_settings(sec.get("settings"))
            blocks = sec.get("blocks")
            if isinstance(blocks, dict):
                for block in blocks.values():
                    walk_block(block)

    templates = result.get("templates")
    if isinstance(templates, dict):
        for tpl in templates.values():
            if isinstance(tpl, dict):
                walk_sections(tpl.get("sections"))

    groups = result.get("section_groups")
    if isinstance(groups, dict):
        for grp in groups.values():
            if isinstance(grp, dict):
                walk_sections(grp.get("sections"))

    walk_settings(result.get("global_settings"))
    return result
