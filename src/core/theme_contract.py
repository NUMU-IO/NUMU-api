"""Server-side theme-contract gate.

Mirrors the JS validators in ``@numueg/theme-sdk/validation`` so the platform
can refuse a built theme bundle that doesn't match what the storefront
expects — independent of (and defending against a bypassed/old) CLI or plugin.

This validates the BUILT artifacts the plugin emits:
  * ``dist/manifest.json``   — embedded manifest + section_schemas
  * ``dist/import-map.json`` — federation + contract-version descriptor

Run it in the build worker after the bundle is produced; fail the build when
it returns errors.

Keep ``HOST_CONTRACT_VERSION`` in lockstep with the SDK's
``THEME_CONTRACT_VERSION`` and the plugin's ``THEME_CONTRACT_VERSION``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# The highest theme-contract version this platform can render. A bundle built
# for a NEWER contract is refused (the host wouldn't know how to render it).
HOST_CONTRACT_VERSION = 1

_SECTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def _is_obj(v: Any) -> bool:
    return isinstance(v, dict)


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _collect_preset_section_types(manifest: dict) -> set[str]:
    """Section types referenced by the manifest's presets."""
    types: set[str] = set()
    presets = manifest.get("presets")
    if not _is_obj(presets):
        return types
    for bucket in (presets.get("templates"), presets.get("section_groups")):
        if not _is_obj(bucket):
            continue
        for entry in bucket.values():
            if not _is_obj(entry):
                continue
            sections = entry.get("sections")
            if isinstance(sections, list):
                instances = sections
            elif _is_obj(sections):
                instances = list(sections.values())
            else:
                instances = []
            for inst in instances:
                if _is_obj(inst) and _nonempty_str(inst.get("type")):
                    types.add(inst["type"])
    return types


def validate_manifest_core(manifest: dict) -> list[str]:
    """Required manifest fields shared by source + built manifests."""
    errors: list[str] = []
    mid = manifest.get("id")
    if not _nonempty_str(mid):
        errors.append("manifest: missing `id`")
    elif not _THEME_ID_RE.match(mid):
        errors.append(
            f"manifest: id '{mid}' must be lowercase alphanumeric with "
            "dashes/underscores (no leading/trailing separator)"
        )
    name = manifest.get("name")
    name_ok = _nonempty_str(name) or (
        _is_obj(name) and any(_nonempty_str(v) for v in name.values())
    )
    if not name_ok:
        errors.append("manifest: missing a non-empty `name`")
    version = manifest.get("version")
    if not _nonempty_str(version):
        errors.append("manifest: missing `version`")
    elif not _SEMVER_RE.match(version):
        errors.append(f"manifest: version '{version}' is not valid semver")
    if not _nonempty_str(manifest.get("author")):
        errors.append("manifest: missing `author`")
    return errors


def validate_built_manifest(
    manifest: dict,
    import_map: dict | None = None,
    *,
    host_contract_version: int = HOST_CONTRACT_VERSION,
) -> list[str]:
    """Validate the emitted dist/manifest.json (+ optional import-map.json).

    Returns a list of human-readable error strings (empty when valid).
    """
    errors: list[str] = []
    if not _is_obj(manifest):
        return ["manifest.json must be a JSON object"]

    errors.extend(validate_manifest_core(manifest))

    schemas = manifest.get("section_schemas")
    shipped = set(schemas.keys()) if _is_obj(schemas) else set()
    if not _is_obj(schemas):
        errors.append("manifest: missing `section_schemas`")

    for stype in _collect_preset_section_types(manifest):
        if stype not in shipped:
            errors.append(
                f"manifest: preset references section type '{stype}' "
                "not present in section_schemas"
            )

    # Validate each shipped section schema (type format + filename match).
    if _is_obj(schemas):
        for stype, schema in schemas.items():
            if not _is_obj(schema):
                errors.append(f"section_schemas['{stype}'] must be an object")
                continue
            decl = schema.get("type")
            if not _nonempty_str(decl):
                errors.append(f"section_schemas['{stype}']: missing `type`")
            else:
                if not _SECTION_TYPE_RE.match(decl):
                    errors.append(
                        f"section_schemas['{stype}']: type '{decl}' must be "
                        "lowercase and start with a letter"
                    )
                if decl != stype:
                    errors.append(
                        f"section_schemas['{stype}']: type '{decl}' must equal "
                        f"its key '{stype}' (filename = schema-type convention)"
                    )
            if not _nonempty_str(schema.get("name")):
                errors.append(f"section_schemas['{stype}']: missing `name`")

    # Contract-version compatibility from the import map.
    if import_map is not None:
        if not _is_obj(import_map):
            errors.append("import-map.json must be a JSON object")
        else:
            cv = import_map.get("contract_version")
            if isinstance(cv, int) and cv > host_contract_version:
                errors.append(
                    f"bundle built for theme-contract v{cv}; this platform "
                    f"supports up to v{host_contract_version}"
                )

    return errors


def validate_dist_bundle(
    dist_dir: str | Path,
    *,
    host_contract_version: int = HOST_CONTRACT_VERSION,
) -> list[str]:
    """Read dist/manifest.json + dist/import-map.json and validate them.

    A missing/unreadable manifest is a hard error (the plugin always emits it).
    A missing import-map is tolerated (older plugin) — only the contract-version
    check is skipped.
    """
    dist = Path(dist_dir)
    manifest_path = dist / "manifest.json"
    if not manifest_path.exists():
        return ["dist/manifest.json not found — the theme plugin must emit it"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"dist/manifest.json is not valid JSON: {exc}"]

    import_map: dict | None = None
    import_map_path = dist / "import-map.json"
    if import_map_path.exists():
        try:
            import_map = json.loads(import_map_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            import_map = None

    return validate_built_manifest(
        manifest, import_map, host_contract_version=host_contract_version
    )


# ── Navigability (guarantee G2) ──────────────────────────────────────────────
# A theme must ship its own chrome — at least one header and one footer
# section — or every shopper gets the host's generic `ByotChromeFallback`
# strip (the "unnavigable theme" bug class). Detection mirrors the host's
# `byotProvidesOwnChrome()` (numu-storefront resolve-theme.ts) and the CLI's
# `navigability` lint rule: a schema `tag` of "header"/"footer" is the
# declared signal; a chrome-looking section-type NAME is the legacy match.

_HEADER_NAME_RE = re.compile(
    r"(?:^|[-_])(?:header|navbar|topbar)(?:$|[-_])|header$", re.IGNORECASE
)
_FOOTER_NAME_RE = re.compile(r"(?:^|[-_])footer(?:$|[-_])|footer$", re.IGNORECASE)


def _chrome_kinds_present(
    section_schemas: dict[str, dict],
) -> dict[str, bool]:
    found = {"header": False, "footer": False}
    for stype, schema in section_schemas.items():
        tag = schema.get("tag") if _is_obj(schema) else None
        tag = tag.lower() if isinstance(tag, str) else ""
        if tag in found:
            found[tag] = True
            continue
        if not tag:
            if _HEADER_NAME_RE.search(stype):
                found["header"] = True
            if _FOOTER_NAME_RE.search(stype):
                found["footer"] = True
    return found


def validate_navigability_source(theme_dir: str | Path) -> list[str]:
    """Check an extracted SOURCE tree ships header + footer sections.

    Reads ``schemas/sections/*.json`` — the same data the CLI rule uses —
    so the guarantee holds even when the CLI linter is unavailable or
    predates the rule. Returns human-readable error strings (empty = ok).
    """
    schemas_dir = Path(theme_dir) / "schemas" / "sections"
    section_schemas: dict[str, dict] = {}
    if schemas_dir.is_dir():
        for schema_file in schemas_dir.glob("*.json"):
            try:
                parsed = json.loads(schema_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if _is_obj(parsed):
                stype = parsed.get("type") or schema_file.stem
                section_schemas[str(stype)] = parsed

    found = _chrome_kinds_present(section_schemas)
    errors: list[str] = []
    for kind in ("header", "footer"):
        if not found[kind]:
            errors.append(
                f"theme declares no {kind} section (no schemas/sections entry "
                f'with "tag": "{kind}") — shoppers would get the host\'s '
                "generic fallback strip instead of the theme's own chrome"
            )
    return errors
