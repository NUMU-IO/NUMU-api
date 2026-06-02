"""Theme update classification — Phase 5.1.

When a theme publishes a new version, we diff its schemas against the version
a merchant currently has installed and classify the update the way Shopify
does:

  • **manual**   — a change that could invalidate a merchant's stored
                   customization, so it needs human review before applying:
                     - a setting's ``id`` was removed
                     - a setting's ``type`` changed
                     - a ``range`` setting's ``min`` increased or ``max``
                       decreased (a previously-valid value may now be
                       out of bounds)
                     - a section type was removed
                     - a block type was removed (within a section)
  • **automatic** — everything else (new settings/sections/blocks, relabels,
                   default/info tweaks, a widened range): safe to apply
                   without losing data.

The classifier is pure (two schema dicts in, a verdict out) so it's trivially
testable and reusable by both the on-publish notification generator and any
ad-hoc "what changed?" preview. It NEVER mutates a store — applying an update
is a separate, snapshot-first step via ThemeActivationService.
"""

from __future__ import annotations

from typing import Any

# Setting "types" that are layout dividers, not real inputs — they carry no
# ``id`` and can't hold a stored value, so changes to them are never breaking.
_NON_INPUT_TYPES = {"header", "paragraph"}


def _settings_by_id(schema: Any) -> dict[str, dict[str, Any]]:
    """Flatten a settings schema (flat list, list-of-groups, or a
    ``{settings: [...]}`` wrapper) into ``{setting_id: setting_def}``.

    Skips divider entries (header/paragraph) and anything without an ``id``.
    """
    out: dict[str, dict[str, Any]] = {}
    if isinstance(schema, dict) and isinstance(schema.get("settings"), list):
        schema = schema["settings"]
    if not isinstance(schema, list):
        return out
    for item in schema:
        if not isinstance(item, dict):
            continue
        # A group: {name, settings: [...]} — recurse one level.
        if isinstance(item.get("settings"), list) and "id" not in item:
            for s in item["settings"]:
                if (
                    isinstance(s, dict)
                    and s.get("id")
                    and s.get("type") not in _NON_INPUT_TYPES
                ):
                    out[str(s["id"])] = s
        elif item.get("id") and item.get("type") not in _NON_INPUT_TYPES:
            out[str(item["id"])] = item
    return out


def _blocks_by_type(blocks: Any) -> dict[str, dict[str, Any]]:
    """Normalize a section's blocks (Shopify list-of-``{type,...}`` or a
    ``{type: def}`` map) into ``{block_type: block_def}``."""
    out: dict[str, dict[str, Any]] = {}
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict) and b.get("type"):
                out[str(b["type"])] = b
    elif isinstance(blocks, dict):
        for t, b in blocks.items():
            if isinstance(b, dict):
                out[str(t)] = b
    return out


def _diff_settings(
    old: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
    scope: str,
    changes: list[dict[str, Any]],
) -> None:
    """Append change records for a single settings map (global / section /
    block) following Shopify's manual-vs-automatic rules."""
    for sid, sdef in old.items():
        target = f"{scope}.setting:{sid}"
        if sid not in new:
            changes.append({
                "kind": "setting_removed",
                "target": target,
                "breaking": True,
                "detail": f"Setting '{sid}' was removed.",
            })
            continue
        ndef = new[sid]
        old_type = sdef.get("type")
        new_type = ndef.get("type")
        if old_type != new_type:
            changes.append({
                "kind": "setting_type_changed",
                "target": target,
                "breaking": True,
                "detail": f"Setting '{sid}' type changed: {old_type} → {new_type}.",
            })
            continue
        if old_type == "range":
            tightened = _range_tightened(sdef, ndef)
            if tightened:
                changes.append({
                    "kind": "range_tightened",
                    "target": target,
                    "breaking": True,
                    "detail": f"Setting '{sid}' range tightened ({tightened}).",
                })
    for sid in new:
        if sid not in old:
            changes.append({
                "kind": "setting_added",
                "target": f"{scope}.setting:{sid}",
                "breaking": False,
                "detail": f"Setting '{sid}' was added.",
            })


def _range_tightened(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    """Return a description if the new range is narrower (min↑ or max↓), else
    None. A widened range is non-breaking (every old value still valid)."""
    try:
        omin, omax = float(old.get("min")), float(old.get("max"))
        nmin, nmax = float(new.get("min")), float(new.get("max"))
    except (TypeError, ValueError):
        return None
    parts = []
    if nmin > omin:
        parts.append(f"min {omin}→{nmin}")
    if nmax < omax:
        parts.append(f"max {omax}→{nmax}")
    return ", ".join(parts) if parts else None


def classify_theme_update(
    old_schemas: dict[str, Any] | None,
    new_schemas: dict[str, Any] | None,
) -> dict[str, Any]:
    """Diff installed (``old``) vs new theme schemas → an update verdict.

    Each ``*_schemas`` dict is ``{"settings_schema": <list|groups>,
    "section_schemas": {type: {settings, blocks}}}`` (the shape stored on the
    ``themes`` table / ``external_theme``).

    Returns ``{"classification": "manual"|"automatic", "breaking": bool,
    "changes": [ {kind, target, breaking, detail}, ... ]}``. ``manual`` iff any
    change is breaking; otherwise ``automatic`` (even with zero changes).
    """
    old = old_schemas or {}
    new = new_schemas or {}
    changes: list[dict[str, Any]] = []

    # 1) Global theme settings.
    _diff_settings(
        _settings_by_id(old.get("settings_schema")),
        _settings_by_id(new.get("settings_schema")),
        "global",
        changes,
    )

    # 2) Section schemas (+ their settings + their blocks).
    old_secs = (
        old.get("section_schemas")
        if isinstance(old.get("section_schemas"), dict)
        else {}
    )
    new_secs = (
        new.get("section_schemas")
        if isinstance(new.get("section_schemas"), dict)
        else {}
    )

    for stype, sdef in old_secs.items():
        if stype not in new_secs:
            changes.append({
                "kind": "section_removed",
                "target": f"section:{stype}",
                "breaking": True,
                "detail": f"Section '{stype}' was removed.",
            })
            continue
        ndef = new_secs[stype]
        sdef = sdef if isinstance(sdef, dict) else {}
        ndef = ndef if isinstance(ndef, dict) else {}
        _diff_settings(
            _settings_by_id(sdef.get("settings")),
            _settings_by_id(ndef.get("settings")),
            f"section:{stype}",
            changes,
        )
        old_blocks = _blocks_by_type(sdef.get("blocks"))
        new_blocks = _blocks_by_type(ndef.get("blocks"))
        for btype, bdef in old_blocks.items():
            if btype not in new_blocks:
                changes.append({
                    "kind": "block_removed",
                    "target": f"section:{stype}.block:{btype}",
                    "breaking": True,
                    "detail": f"Block '{btype}' was removed from section '{stype}'.",
                })
                continue
            _diff_settings(
                _settings_by_id(bdef.get("settings")),
                _settings_by_id(new_blocks[btype].get("settings")),
                f"section:{stype}.block:{btype}",
                changes,
            )
        for btype in new_blocks:
            if btype not in old_blocks:
                changes.append({
                    "kind": "block_added",
                    "target": f"section:{stype}.block:{btype}",
                    "breaking": False,
                    "detail": f"Block '{btype}' was added to section '{stype}'.",
                })

    for stype in new_secs:
        if stype not in old_secs:
            changes.append({
                "kind": "section_added",
                "target": f"section:{stype}",
                "breaking": False,
                "detail": f"Section '{stype}' was added.",
            })

    breaking = any(c["breaking"] for c in changes)
    return {
        "classification": "manual" if breaking else "automatic",
        "breaking": breaking,
        "changes": changes,
    }
