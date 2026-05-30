"""Generate initial V3 customization from theme presets.

Used when a merchant activates a BYOT theme (reads presets from theme.json)
or when a built-in theme is first initialized.

Also exposes ``reconcile_v3_customization`` — a read-time, non-destructive
repair that swaps any page template whose sections the active theme can't
render (empty, or all-unknown types) for the theme's preset template. This
mirrors the storefront bundle's own ``selectTemplateSections`` fallback so the
editor's section list always reflects exactly what the storefront renders.
"""

from __future__ import annotations

import copy
from typing import Any

from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)


def _build_sections_from_list(
    section_list: list[dict[str, Any]] | None,
) -> tuple[dict[str, SectionInstance], list[str]]:
    """Build a `{section_id: SectionInstance}` map + ordered id list from a
    preset's ordered ``sections`` array. Section ids are deterministic
    (``<type>-<idx>``, 0-based — see the scheme note below) so repeated calls
    for the same preset are stable, and the editor + storefront derive
    identical ids from the same preset.
    """
    sections: dict[str, SectionInstance] = {}
    order: list[str] = []
    for idx, section_data in enumerate(section_list or []):
        if not isinstance(section_data, dict):
            continue
        # Section id scheme MUST match the storefront (resolve-theme.ts
        # `normalisePreset`) and the bundle (`resolveSections`): `<type>-<idx>`
        # with a 0-based index. The editor draft (this reconcile/seed) and the
        # storefront preview must derive identical ids from the same preset, or
        # a preview click reports an id the editor can't resolve → "No section
        # selected". (Was `<type>_<idx+1>`, which mismatched the preview.)
        section_id = f"{section_data.get('type', 'section')}-{idx}"
        blocks: dict[str, BlockInstance] = {}
        block_order: list[str] = []
        for bidx, block_data in enumerate(section_data.get("blocks", []) or []):
            if not isinstance(block_data, dict):
                continue
            block_id = f"{block_data.get('type', 'block')}-{bidx}"
            blocks[block_id] = BlockInstance(
                type=block_data.get("type", "text"),
                settings=block_data.get("settings", {}),
            )
            block_order.append(block_id)
        sections[section_id] = SectionInstance(
            type=section_data.get("type", "generic"),
            settings=section_data.get("settings", {}),
            blocks=blocks,
            block_order=block_order,
        )
        order.append(section_id)
    return sections, order


def _known_section_types(section_schemas: Any) -> set[str]:
    """Return the set of section types the active theme can render, from its
    ``section_schemas`` (dict keyed by type, or Shopify-style list of
    ``{type, ...}``). Empty set means "no schema info" — callers treat that as
    "can't judge, keep existing".
    """
    if isinstance(section_schemas, dict):
        return {str(k) for k in section_schemas.keys()}
    if isinstance(section_schemas, list):
        return {
            str(e["type"])
            for e in section_schemas
            if isinstance(e, dict) and e.get("type")
        }
    return set()


def generate_initial_v3_customization(
    theme_id: str,
    presets: dict[str, Any] | None = None,
    bundle_url: str | None = None,
    css_url: str | None = None,
    settings_schema: dict[str, Any] | None = None,
    section_schemas: dict[str, Any] | None = None,
    mode: str = "production",
    error_template_url: str | None = None,
    loading_template_url: str | None = None,
) -> ThemeSettingsV3:
    """Generate a V3 customization payload from theme presets.

    For BYOT themes: reads the presets field from theme.json.
    For built-in themes: generates a sensible default home template.
    """
    templates: dict[str, PageTemplate] = {}
    section_groups: dict[str, SectionGroup] = {}
    global_settings: dict[str, Any] = {}

    if presets:
        # Extract global settings defaults from settings_schema
        if settings_schema and isinstance(settings_schema, list):
            for setting in settings_schema:
                if (
                    isinstance(setting, dict)
                    and "id" in setting
                    and "default" in setting
                ):
                    global_settings[setting["id"]] = setting["default"]

        # Build templates from presets
        preset_templates = presets.get("templates", {})
        for tpl_name, tpl_data in preset_templates.items():
            sections, order = _build_sections_from_list(tpl_data.get("sections", []))
            templates[tpl_name] = PageTemplate(
                name=tpl_data.get("name", tpl_name.title()),
                sections=sections,
                order=order,
            )

        # Build section groups from presets
        preset_groups = presets.get("section_groups", {})
        for group_name, group_data in preset_groups.items():
            grp_sections, grp_order = _build_sections_from_list(
                group_data.get("sections", [])
            )
            section_groups[group_name] = SectionGroup(
                name=group_data.get("name", group_name.title()),
                sections=grp_sections,
                order=grp_order,
            )

    # Ensure default header/footer groups exist. Kept unconditional (built-in
    # AND BYOT) for backward-compatibility. BYOT themes that model header/footer
    # as in-template sections (e.g. bon-younes' by-header/by-footer) would get
    # phantom generic "Header/Footer" groups here, but those are stripped at
    # read time — by reconcile_v3_customization (editor) and the storefront's
    # resolve-theme sanitiser (SSR) — because their "header"/"footer" types
    # aren't in the theme's section_schemas. So the editor/preview stay clean
    # without coupling the seed to a per-theme structural assumption.
    if "header" not in section_groups:
        section_groups["header"] = SectionGroup(
            name="Header Group",
            sections={"header_1": SectionInstance(type="header", settings={})},
            order=["header_1"],
        )
    if "footer" not in section_groups:
        section_groups["footer"] = SectionGroup(
            name="Footer Group",
            sections={"footer_1": SectionInstance(type="footer", settings={})},
            order=["footer_1"],
        )

    # For built-in themes without presets, generate a default home template
    if not presets and not templates:
        templates["home"] = PageTemplate(
            name="Home",
            sections={
                "hero_1": SectionInstance(type="hero", settings={}),
                "featured_1": SectionInstance(type="featured-products", settings={}),
            },
            order=["hero_1", "featured_1"],
        )

    # Build external theme metadata for BYOT. `mode` controls the bundle-URL
    # allowlist: "development" permits localhost, "production" requires the
    # configured CDN hosts.
    external_theme = None
    if bundle_url:
        external_theme = ExternalThemeMetadata(
            bundle_url=bundle_url,
            css_url=css_url,
            settings_schema=settings_schema,
            section_schemas=section_schemas,
            mode="development" if mode == "development" else "production",
            error_template_url=error_template_url,
            loading_template_url=loading_template_url,
        )

    return ThemeSettingsV3(
        schema_version=3,
        theme_id=theme_id,
        global_settings=global_settings,
        templates=templates,
        section_groups=section_groups,
        external_theme=external_theme,
    )


def reconcile_v3_customization(
    customization: dict[str, Any] | None,
    section_schemas: Any,
    presets: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return ``customization`` with any unrenderable page template (and
    phantom section group) replaced by the active theme's preset.

    This is the server-side mirror of the storefront bundle's
    ``selectTemplateSections`` (see bon-younes ``main.tsx``): a template whose
    sections are ALL of types the theme doesn't know (e.g. a stale ``hero`` /
    ``featured-products`` left over from a previous theme, or from a snapshot
    restore) is dropped in favour of the theme's bundled preset. So the editor
    lists exactly the sections the storefront actually renders, and they
    resolve schemas (→ become editable), for ANY theme.

    Contract:
    - **Non-destructive**: the input dict is never mutated; a new dict is
      returned only when something changed, otherwise the original is returned
      unchanged (cheap no-op for the common, already-aligned case).
    - **No clobber**: a template with at least one theme-known section type is
      left untouched, preserving real merchant edits. Only empty or
      fully-unknown templates are replaced.
    - If the theme exposes no presets, or no section-schema info to judge
      against, the customization is returned unchanged.
    """
    if not isinstance(customization, dict) or not isinstance(presets, dict):
        return customization
    preset_templates = presets.get("templates") or {}
    if not preset_templates:
        return customization

    known = _known_section_types(section_schemas)
    changed = False

    templates = customization.get("templates") or {}
    new_templates = dict(templates)
    for tpl_name, tpl_data in preset_templates.items():
        existing = templates.get(tpl_name) or {}
        sections = existing.get("sections") or {}
        if sections:
            if not known:
                continue  # can't judge type-compat → keep existing
            types = [s.get("type") for s in sections.values() if isinstance(s, dict)]
            if any(t in known for t in types):
                continue  # has at least one renderable section → keep (no clobber)
        # empty/missing OR all-unknown → derive from the theme's preset
        secs, order = _build_sections_from_list(tpl_data.get("sections", []))
        new_templates[tpl_name] = PageTemplate(
            name=tpl_data.get("name", tpl_name.title()),
            sections=secs,
            order=order,
        ).model_dump()
        changed = True

    # Phantom section groups: V3 bundles render header/footer from templates,
    # not from section_groups. If the stored groups hold only types the theme
    # can't render (the generic header/footer left by legacy seeding), rebuild
    # them from the theme's preset groups (usually none → cleared), so the
    # editor stops showing un-editable phantom groups.
    new_groups = None
    groups = customization.get("section_groups") or {}
    if groups and known:
        group_types = [
            s.get("type")
            for g in groups.values()
            if isinstance(g, dict)
            for s in (g.get("sections") or {}).values()
            if isinstance(s, dict)
        ]
        if group_types and not any(t in known for t in group_types):
            preset_groups = presets.get("section_groups") or {}
            rebuilt: dict[str, Any] = {}
            for gname, gdata in preset_groups.items():
                secs, order = _build_sections_from_list(gdata.get("sections", []))
                rebuilt[gname] = SectionGroup(
                    name=gdata.get("name", gname.title()),
                    sections=secs,
                    order=order,
                ).model_dump()
            new_groups = rebuilt
            changed = True

    if not changed:
        return customization

    result = copy.deepcopy(customization)
    result["templates"] = new_templates
    if new_groups is not None:
        result["section_groups"] = new_groups
    return result
