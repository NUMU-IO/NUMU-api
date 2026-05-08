"""V3 Theme Settings Pydantic models.

Defines the canonical V3 data model for theme customization.
Separates global settings from page-specific templates,
supports blocks inside sections, and section groups for header/footer.
"""

from __future__ import annotations

import os
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

# ── BYOT bundle URL allowlist ─────────────────────────────────────────────────
#
# Tenants must NOT be able to point a storefront at arbitrary JS — this is the
# entire reason BYOT bundles go through the marketplace build pipeline (which
# uploads to our R2/S3 bucket). The allowlist below restricts where bundle and
# CSS URLs may originate.
#
# - Production: HTTPS hosts under `*.numueg.app`, the configured public R2/S3
#   URL, and any extra hosts listed in NUMU_BYOT_BUNDLE_HOSTS (comma-separated).
# - Development: `localhost` and `127.0.0.1` (Vite dev server) on http/https.
#
# The check runs at validation time on every autosave/publish, so a merchant
# token leak can't be used to inject a malicious script into the storefront.

_DEFAULT_PROD_HOSTS = (
    "numueg.app",
    "cdn.numueg.app",
    "themes.numueg.app",
    "r2.numueg.app",
)
_DEV_HOSTS = ("localhost", "127.0.0.1")


def _allowed_bundle_hosts() -> tuple[set[str], set[str]]:
    """Return (prod_hosts, prod_suffixes) populated from env + defaults.

    A "suffix" matches any subdomain — `numueg.app` matches
    `cdn.numueg.app` and `cdn.themes.numueg.app`.
    """
    hosts: set[str] = set()
    suffixes: set[str] = set()

    # Built-in defaults: numueg.app and well-known subdomains
    suffixes.add("numueg.app")
    hosts.update(_DEFAULT_PROD_HOSTS)

    # The configured S3/R2 public URL (if any) — used by our build pipeline
    public_url = os.getenv("S3_PUBLIC_URL") or os.getenv("R2_PUBLIC_URL", "")
    if public_url:
        try:
            host = urlparse(public_url).hostname
            if host:
                hosts.add(host.lower())
        except Exception:
            pass

    # Operator-configured extras
    extras = os.getenv("NUMU_BYOT_BUNDLE_HOSTS", "")
    for h in (h.strip().lower() for h in extras.split(",") if h.strip()):
        if h.startswith("*."):
            suffixes.add(h[2:])
        else:
            hosts.add(h)

    return hosts, suffixes


def _is_allowed_bundle_url(url: str, mode: str) -> bool:
    """Validate that `url` points at an approved host for the given mode."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if not parsed.scheme or not parsed.hostname:
        return False
    host = parsed.hostname.lower()

    # Dev mode: localhost only, http or https
    if mode == "development":
        if parsed.scheme not in ("http", "https"):
            return False
        return host in _DEV_HOSTS

    # Production mode: HTTPS only, allowlisted host or suffix
    if parsed.scheme != "https":
        return False

    hosts, suffixes = _allowed_bundle_hosts()
    if host in hosts:
        return True
    return any(host == s or host.endswith("." + s) for s in suffixes)


# ── Models ────────────────────────────────────────────────────────────────────


class BlockInstance(BaseModel):
    """A single block within a section."""

    type: str  # e.g., "button", "heading", "@app/reviews/star-rating"
    disabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_block_type(cls, v: str) -> str:
        if v.startswith("@app/"):
            parts = v.split("/")
            if len(parts) < 3:
                raise ValueError(
                    f"@app block type must be '@app/{{slug}}/{{type}}', got '{v}'"
                )
        return v


class SectionInstance(BaseModel):
    """A section placed in a page template or section group."""

    type: str  # e.g., "hero", "featured-collection"
    disabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    blocks: dict[str, BlockInstance] = Field(default_factory=dict)
    block_order: list[str] = Field(default_factory=list)


class PageTemplate(BaseModel):
    """A page template defining which sections appear and in what order."""

    name: str  # e.g., "Home", "Product"
    sections: dict[str, SectionInstance] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class SectionGroup(BaseModel):
    """A persistent section group (header, footer).

    Section groups are rendered on every page and can contain
    multiple sections (e.g., announcement bar + header).
    """

    name: str  # e.g., "Header Group", "Footer Group"
    sections: dict[str, SectionInstance] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class ExternalThemeMetadata(BaseModel):
    """Metadata for BYOT (external) themes.

    `bundle_url` and `css_url` are validated against an allowlist so a
    leaked merchant token can't inject arbitrary JavaScript into the
    storefront. Production URLs must be HTTPS and live under one of the
    approved CDN hosts; development URLs must be on localhost.
    """

    bundle_url: str
    css_url: str | None = None
    # Shopify-style schema is a *list* of setting defs; legacy callers may
    # pass a wrapped dict. JSONB on the DB side accepts either.
    settings_schema: list[Any] | dict[str, Any] | None = None
    section_schemas: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    mode: Literal["production", "development"] | None = "production"
    dev_url: str | None = None  # Local Vite dev server URL

    @model_validator(mode="after")
    def _enforce_bundle_allowlist(self) -> ExternalThemeMetadata:
        mode = self.mode or "production"
        if not _is_allowed_bundle_url(self.bundle_url, mode):
            raise ValueError(
                f"bundle_url is not on the allowlist for mode={mode}: "
                f"{self.bundle_url!r}"
            )
        if self.css_url and not _is_allowed_bundle_url(self.css_url, mode):
            raise ValueError(
                f"css_url is not on the allowlist for mode={mode}: {self.css_url!r}"
            )
        if self.dev_url and not _is_allowed_bundle_url(self.dev_url, "development"):
            raise ValueError(f"dev_url must be a localhost URL: {self.dev_url!r}")
        return self


class ThemeSettingsV3(BaseModel):
    """The canonical V3 theme configuration payload.

    Written to StoreTheme.customization_v3 / draft_customization_v3.
    """

    schema_version: Literal[3] = 3
    theme_id: str  # "bazar", "modern", or a BYOT UUID
    global_settings: dict[str, Any] = Field(default_factory=dict)
    templates: dict[str, PageTemplate] = Field(default_factory=dict)
    section_groups: dict[str, SectionGroup] = Field(default_factory=dict)
    external_theme: ExternalThemeMetadata | None = None
