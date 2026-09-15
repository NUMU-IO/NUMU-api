"""The NUMU section library: ``lib-*`` sections any V3 theme can offer.

``section-library.json`` is vendored from ``@numueg/theme-sdk``
(``dist/section-library.json``); its ``sdk_version`` records the source. Refresh
it whenever the SDK adds or changes a library section, so the editor and the
storefront's schema filter match the code the storefront serves.

A theme version opts in with ``supports.section_library`` in its theme.json,
for example ``{"version": 1, "replaces": ["lib-ugc-carousel"]}``. The section
code itself ships in the storefront's runtime SDK; this module only decides
which schemas a store sees.

Plan: docs/Plans/theme-section-base/PHASE-3-PLATFORM-WIRING.md § 3.3.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Marketplace versions have no manifest column, so theme.json `supports` rides
# inside the version's `presets` JSON under this key. Activation moves it back
# out into the runtime manifest (see `split_supports`).
SUPPORTS_KEY = "_supports"


@lru_cache(maxsize=1)
def library_sections() -> dict[str, dict[str, Any]]:
    """Library schemas keyed by section type."""
    path = Path(__file__).with_name("section-library.json")
    return json.loads(path.read_text(encoding="utf-8"))["sections"]


def merge_section_library(section_schemas: Any, supports: Any) -> Any:
    """Add the library's schemas when ``supports`` opts in; mutates and returns.

    Pass a copy. Theme-owned types always win, and types listed in
    ``section_library.replaces`` are skipped (a theme that keeps its own
    component for a library section). Flat ``{type: schema}`` maps and
    ``{"sections": {...}, "blocks": {...}}`` envelopes are both handled.

    Empty or non-dict schemas are left alone. An empty map means "no schema
    info", which reconcile treats as "keep the merchant's templates"
    (``theme_v3_presets._known_section_types``); a library-only map would
    instead make every theme section look unrenderable.
    """
    opt_in = supports.get("section_library") if isinstance(supports, dict) else None
    if not opt_in or not isinstance(section_schemas, dict) or not section_schemas:
        return section_schemas
    replaces = set(opt_in.get("replaces") or []) if isinstance(opt_in, dict) else set()
    inner = section_schemas.get("sections")
    target = inner if isinstance(inner, dict) else section_schemas
    for section_type, schema in library_sections().items():
        if section_type not in target and section_type not in replaces:
            target[section_type] = copy.deepcopy(schema)
    return section_schemas


def pack_supports(presets: Any, manifest: Any) -> Any:
    """``presets`` carrying theme.json ``supports`` under ``SUPPORTS_KEY``."""
    supports = manifest.get("supports") if isinstance(manifest, dict) else None
    if isinstance(presets, dict) and isinstance(supports, dict):
        return {**presets, SUPPORTS_KEY: supports}
    return presets


def split_supports(presets: Any) -> tuple[Any, dict[str, Any] | None]:
    """``(presets without SUPPORTS_KEY, supports or None)``."""
    if not isinstance(presets, dict) or SUPPORTS_KEY not in presets:
        return presets, None
    rest = {k: v for k, v in presets.items() if k != SUPPORTS_KEY}
    supports = presets[SUPPORTS_KEY]
    return rest, supports if isinstance(supports, dict) else None
