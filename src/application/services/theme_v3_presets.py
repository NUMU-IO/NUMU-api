"""Generate initial V3 customization from theme presets.

Used when a merchant activates a BYOT theme (reads presets from theme.json)
or when a built-in theme is first initialized.
"""

from __future__ import annotations

from typing import Any

from src.core.entities.theme_settings_v3 import (
    BlockInstance,
    ExternalThemeMetadata,
    PageTemplate,
    SectionGroup,
    SectionInstance,
    ThemeSettingsV3,
)


def generate_initial_v3_customization(
    theme_id: str,
    presets: dict[str, Any] | None = None,
    bundle_url: str | None = None,
    css_url: str | None = None,
    settings_schema: dict[str, Any] | None = None,
    section_schemas: dict[str, Any] | None = None,
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
            sections: dict[str, SectionInstance] = {}
            order: list[str] = []
            for idx, section_data in enumerate(tpl_data.get("sections", [])):
                section_id = f"{section_data.get('type', 'section')}_{idx + 1}"
                blocks: dict[str, BlockInstance] = {}
                block_order: list[str] = []
                for bidx, block_data in enumerate(section_data.get("blocks", [])):
                    block_id = f"{block_data.get('type', 'block')}_{bidx + 1}"
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
            templates[tpl_name] = PageTemplate(
                name=tpl_data.get("name", tpl_name.title()),
                sections=sections,
                order=order,
            )

        # Build section groups from presets
        preset_groups = presets.get("section_groups", {})
        for group_name, group_data in preset_groups.items():
            grp_sections: dict[str, SectionInstance] = {}
            grp_order: list[str] = []
            for idx, section_data in enumerate(group_data.get("sections", [])):
                section_id = f"{section_data.get('type', 'section')}_{idx + 1}"
                grp_sections[section_id] = SectionInstance(
                    type=section_data.get("type", "generic"),
                    settings=section_data.get("settings", {}),
                )
                grp_order.append(section_id)
            section_groups[group_name] = SectionGroup(
                name=group_data.get("name", group_name.title()),
                sections=grp_sections,
                order=grp_order,
            )

    # Ensure default section groups exist
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

    # Build external theme metadata for BYOT
    external_theme = None
    if bundle_url:
        external_theme = ExternalThemeMetadata(
            bundle_url=bundle_url,
            css_url=css_url,
            settings_schema=settings_schema,
            section_schemas=section_schemas,
        )

    return ThemeSettingsV3(
        schema_version=3,
        theme_id=theme_id,
        global_settings=global_settings,
        templates=templates,
        section_groups=section_groups,
        external_theme=external_theme,
    )
