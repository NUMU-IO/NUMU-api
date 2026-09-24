"""``numu.app.json`` v1: validation, the listing projection, change type.

A Partner App version is a manifest. ``ManifestV1`` enforces the rules in
docs/Plans/apps-developer-work/03-PLATFORM-DESIGN.md § 4, and the ``numu app
validate`` CLI runs the same rules by calling the API.

``apps.manifest`` keeps its existing shape (``app_locales``, ``locales``,
``developer``, ``settings_schema`` …) because the hub, the storefront and the
SDK already read it. ``to_listing_manifest`` converts a validated v1 manifest
into that shape when a version is published, so no reader changes.

Scope strings are the public API's own (``orders:read`` …): one vocabulary
for private integrations and Partner Apps (plan 06 D5).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from src.application.services.personal_access_token_service import VALID_SCOPES
from src.core.entities.webhook import SUBSCRIBABLE_EVENT_TYPES

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

#: Events an app may subscribe to: the public webhook events, plus the app
#: lifecycle event every app must handle and ``store.redact`` (48 hours after
#: uninstall: delete the store's data; PDPL 151/2020).
APP_WEBHOOK_EVENTS = frozenset(e.value for e in SUBSCRIBABLE_EVENT_TYPES) | {
    "app.uninstalled",
    "store.redact",
}
#: Delivered directly by NUMU (app_webhooks), never through a subscription.
APP_LIFECYCLE_EVENTS = frozenset({"app.uninstalled", "store.redact"})

#: Scopes an app may request (plan 03 § 5):
#: - the PAT scope strings, minus ``*`` (a PAT convenience, never an app
#:   grant), ``themes:write`` (publishing changes a live storefront),
#:   ``risk:write`` (``risk`` is read-only for apps) and both ``settings``
#:   scopes. ``settings`` is one domain for payment-gateway credentials,
#:   payment proofs, billing, invoices, storefront publishing and the store's
#:   other app installations, which plan 03 § 5 makes never grantable. Add a
#:   narrower scope (e.g. shipping zones) when an app needs one;
#: - plus ``messages``, which alone reaches customer conversations (threads,
#:   messages, channels, WhatsApp) for app tokens. A PAT reaches those with
#:   ``marketing``, unchanged.
APP_SCOPES = (
    frozenset(VALID_SCOPES)
    - {"*", "themes:write", "risk:write", "settings:read", "settings:write"}
) | {"messages:read", "messages:write"}

#: The read scope an app needs to receive each event: an order event carries
#: the shopper's name, phone and address. Built from every subscribable event,
#: so a new event domain fails here at import instead of reaching apps unscoped.
_EVENT_DOMAIN_SCOPES = {"order": "orders:read", "product": "catalog:read"}
EVENT_SCOPES = {
    e.value: _EVENT_DOMAIN_SCOPES[e.value.split(".", 1)[0]]
    for e in SUBSCRIBABLE_EVENT_TYPES
}

#: Scopes that reach personal data about identifiable shoppers, so the app
#: must publish a privacy policy (every ``:write`` scope needs one too).
PERSONAL_DATA_SCOPES = frozenset({
    "customers:read",
    "orders:read",
    "risk:read",
    "messages:read",
})


def app_subscriptions(
    webhooks: list[dict[str, str]], granted: list[str] | None
) -> dict[str, list[str]]:
    """The subscriptions an installation gets: ``{url: sorted events}``.

    Lifecycle events are delivered directly (app_webhooks) and need none. An
    event is subscribed only when its read scope was granted: an app the
    merchant didn't let read orders must not receive them as webhooks either.
    """
    by_url: dict[str, set[str]] = {}
    for hook in webhooks:
        event = hook["event"]
        if event not in APP_LIFECYCLE_EVENTS and EVENT_SCOPES.get(event) in (
            granted or []
        ):
            by_url.setdefault(hook["url"], set()).add(event)
    return {url: sorted(events) for url, events in by_url.items()}


CATEGORIES = (
    "shipping",
    "marketing",
    "sales",
    "customer_support",
    "inventory",
    "analytics",
    "payments",
    "store_design",
    "productivity",
    "other",
)

#: SchemaFormV3 field types a Partner App may use. No raw ``html`` and no
#: resource pickers: a partner's settings form is plain configuration.
SETTING_TYPES = frozenset({
    "text",
    "textarea",
    "number",
    "range",
    "color",
    "checkbox",
    "select",
    "radio",
    "url",
    "header",
    "paragraph",
})
#: Decorative types carry no value, so they need no id.
DECORATIVE_TYPES = frozenset({"header", "paragraph"})


def _https(url: str) -> str:
    """Public https only. Offline check; the submit step also resolves DNS
    with the webhook SSRF guard (``assert_webhook_target``)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"must be an https:// URL: {url}")
    host = parsed.hostname
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError(f"must be a public host: {url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not ip.is_global:
        raise ValueError(f"must be a public host: {url}")
    return url


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Bilingual(_Strict):
    ar: str = Field(min_length=1)
    en: str = Field(min_length=1)

    @model_validator(mode="after")
    def _real_arabic(self):
        if self.ar.strip() == self.en.strip():
            raise ValueError(
                "the Arabic text must be written in Arabic, not copied from the English"
            )
        return self


def _limit(value: Bilingual, n: int, what: str) -> Bilingual:
    if len(value.ar) > n or len(value.en) > n:
        raise ValueError(f"{what} must be {n} characters or fewer in each language")
    return value


class Screenshot(_Strict):
    src: str
    caption: Bilingual | None = None

    @field_validator("src")
    @classmethod
    def _src(cls, v: str) -> str:
        return _https(v)


class Developer(_Strict):
    support_email: EmailStr
    support_url: str | None = None
    privacy_policy_url: str | None = None
    terms_url: str | None = None

    @field_validator("support_url", "privacy_policy_url", "terms_url")
    @classmethod
    def _urls(cls, v: str | None) -> str | None:
        return _https(v) if v else v


class OAuth(_Strict):
    redirect_urls: list[str] = Field(min_length=1, max_length=10)
    scopes: list[str] = Field(min_length=1)
    optional_scopes: list[str] = Field(default_factory=list)

    @field_validator("redirect_urls")
    @classmethod
    def _redirects(cls, v: list[str]) -> list[str]:
        return [_https(u) for u in v]

    @field_validator("scopes", "optional_scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - APP_SCOPES)
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return sorted(set(v))


class Webhook(_Strict):
    event: str
    url: str

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if v not in APP_WEBHOOK_EVENTS:
            raise ValueError(f"unknown webhook event: {v}")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _https(v)


class AppProxy(_Strict):
    """``https://<store>/apps/<subpath>/*`` is fetched server-side from ``url``."""

    subpath: str
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _https(v)


CYCLE_LABELS = {
    "monthly": {"ar": "في الشهر", "en": "/ month"},
    "annual": {"ar": "في السنة", "en": "/ year"},
}


class Pricing(_Strict):
    """``free``; ``external`` (the partner bills the merchant themselves); or
    ``recurring``: NUMU charges the merchant's wallet every cycle and pays the
    partner 80% (app_billing). ``one_time`` is not offered."""

    model: Literal["free", "external", "recurring"]
    #: For ``external``: what the merchant will be charged, shown on the listing.
    #: For ``recurring`` it defaults to the price and cycle.
    label: Bilingual | None = None
    #: ``recurring`` only, in piasters: EGP 5 to EGP 100,000 a cycle.
    price_cents: int | None = Field(default=None, ge=500, le=10_000_000)
    cycle: Literal["monthly", "annual"] | None = None
    currency: Literal["EGP"] = "EGP"

    @model_validator(mode="after")
    def _recurring_has_a_price(self):
        priced = self.price_cents is not None or self.cycle is not None
        if self.model == "recurring" and (self.price_cents is None or not self.cycle):
            raise ValueError(
                "pricing.price_cents and pricing.cycle are required for recurring"
            )
        if self.model != "recurring" and priced:
            raise ValueError(
                "pricing.price_cents and pricing.cycle are for recurring only"
            )
        return self


#: DESIGN.md § 5: Arabic amounts use Arabic-Indic digits and separators,
#: exactly as ``(1250).toLocaleString("ar-EG")`` writes them (``١٬٢٥٠``).
_AR_DIGITS = str.maketrans("0123456789,.", "٠١٢٣٤٥٦٧٨٩٬٫")


def price_label(pricing: dict[str, Any]) -> dict[str, str] | None:
    """The listing's price text in both languages."""
    if pricing.get("label"):
        return pricing["label"]
    if pricing["model"] == "free":
        return {"ar": "مجاني", "en": "Free"}
    if pricing["model"] == "recurring":
        amount = f"{pricing['price_cents'] / 100:,.2f}".removesuffix(".00")
        cycle = CYCLE_LABELS[pricing["cycle"]]
        return {
            "ar": f"{amount.translate(_AR_DIGITS)} ج.م {cycle['ar']}",
            "en": f"EGP {amount} {cycle['en']}",
        }
    return None


class ManifestV1(_Strict):
    manifest_version: Literal[1]
    slug: str
    version: str
    #: Storefront extensions arrive in Phase 8.
    type: list[Literal["connected"]] = Field(min_length=1)
    name: Bilingual
    tagline: Bilingual
    description: Bilingual
    icon: str
    screenshots: list[Screenshot] = Field(default_factory=list, max_length=8)
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    developer: Developer
    app_url: str
    embedded: bool = False
    embedded_path: str | None = Field(default=None, max_length=200)
    app_proxy: AppProxy | None = None
    oauth: OAuth
    webhooks: list[Webhook] = Field(min_length=1)
    settings_schema: list[dict[str, Any]] = Field(default_factory=list, max_length=60)
    pricing: Pricing
    languages: list[Literal["ar", "en"]] = Field(default_factory=lambda: ["ar", "en"])
    # Accept the editor hint without it being a field of the manifest itself.
    schema_: str | None = Field(default=None, alias="$schema")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must match ^[a-z][a-z0-9-]{2,40}$")
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError("version must be semver MAJOR.MINOR.PATCH")
        return v

    @field_validator("icon", "app_url")
    @classmethod
    def _urls(cls, v: str) -> str:
        return _https(v)

    @field_validator("embedded_path")
    @classmethod
    def _embedded_path(cls, v: str | None) -> str | None:
        if v is not None and (not v.startswith("/") or v.startswith("//")):
            raise ValueError("embedded_path must be a path starting with a single /")
        return v

    @field_validator("tagline")
    @classmethod
    def _tagline(cls, v: Bilingual) -> Bilingual:
        return _limit(v, 80, "tagline")

    @field_validator("description")
    @classmethod
    def _description(cls, v: Bilingual) -> Bilingual:
        return _limit(v, 4000, "description")

    @field_validator("settings_schema")
    @classmethod
    def _settings(cls, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        for i, f in enumerate(fields):
            where = f"settings_schema[{i}]"
            kind = f.get("type")
            if kind not in SETTING_TYPES:
                raise ValueError(
                    f"{where}: type must be one of {sorted(SETTING_TYPES)}"
                )
            loc = f.get("locales") or {}
            text_key = "content" if kind in DECORATIVE_TYPES else "label"
            if not (
                (loc.get("ar") or {}).get(text_key)
                and (loc.get("en") or {}).get(text_key)
            ):
                raise ValueError(
                    f"{where}: locales.ar.{text_key} and locales.en.{text_key} are required"
                )
            if kind in DECORATIVE_TYPES:
                continue
            fid = f.get("id")
            if not isinstance(fid, str) or not re.match(r"^[a-z][a-z0-9_]{0,40}$", fid):
                raise ValueError(f"{where}: id must match ^[a-z][a-z0-9_]{{0,40}}$")
            if fid in seen:
                raise ValueError(f"{where}: duplicate id {fid}")
            seen.add(fid)
            if kind in ("select", "radio") and not f.get("options"):
                raise ValueError(f"{where}: {kind} needs options")
        return fields

    @model_validator(mode="after")
    def _rules(self):
        if not any(w.event == "app.uninstalled" for w in self.webhooks):
            raise ValueError("webhooks must include app.uninstalled")
        requested = set(self.oauth.scopes) | set(self.oauth.optional_scopes)
        unscoped = sorted({
            f"{w.event} needs {EVENT_SCOPES[w.event]}"
            for w in self.webhooks
            if w.event in EVENT_SCOPES and EVENT_SCOPES[w.event] not in requested
        })
        if unscoped:
            raise ValueError(f"webhook events need a scope: {'; '.join(unscoped)}")
        sensitive = any(s.endswith(":write") for s in requested) or bool(
            requested & PERSONAL_DATA_SCOPES
        )
        if sensitive and not self.developer.privacy_policy_url:
            raise ValueError(
                "developer.privacy_policy_url is required for any :write scope and for "
                + ", ".join(sorted(PERSONAL_DATA_SCOPES))
            )
        if self.app_proxy and self.app_proxy.subpath != self.slug:
            raise ValueError("app_proxy.subpath must be the app's slug")
        if self.embedded_path and not self.embedded:
            raise ValueError("embedded_path needs embedded: true")
        if self.pricing.model == "external" and not self.pricing.label:
            raise ValueError("pricing.label is required for an external price")
        return self


def semver_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in version.split("."))
    return major, minor, patch


def manifest_urls(m: dict[str, Any]) -> set[str]:
    oauth = m.get("oauth") or {}
    return (
        {m.get("app_url") or ""}
        | set(oauth.get("redirect_urls") or [])
        | {w.get("url") for w in m.get("webhooks") or []}
        | {(m.get("app_proxy") or {}).get("url") or ""}
    ) - {""}


def _scopes(m: dict[str, Any]) -> set[str]:
    oauth = m.get("oauth") or {}
    return set(oauth.get("scopes") or []) | set(oauth.get("optional_scopes") or [])


def change_type(new: dict[str, Any], published: dict[str, Any] | None) -> str:
    """What a reviewer must look at hardest:
    new_app > new_scopes > urls > pricing > listing_only."""
    if published is None:
        return "new_app"
    if _scopes(new) - _scopes(published):
        return "new_scopes"
    if manifest_urls(new) != manifest_urls(published):
        return "urls"
    if (new.get("pricing") or {}) != (published.get("pricing") or {}):
        return "pricing"
    return "listing_only"


def to_listing_manifest(m: dict[str, Any], *, developer_name: str) -> dict[str, Any]:
    """A validated v1 manifest in the shape ``apps.manifest`` readers use."""
    pricing = m["pricing"]
    label = price_label(pricing)
    dev = m["developer"]
    return {
        "version": m["version"],
        "tagline": m["tagline"]["en"],
        "locales": {lang: {"tagline": m["tagline"][lang]} for lang in ("ar", "en")},
        "app_locales": {
            lang: {"name": m["name"][lang], "description": m["description"][lang]}
            for lang in ("ar", "en")
        },
        "developer": {
            "name": developer_name,
            "url": dev.get("support_url"),
            "support_email": dev["support_email"],
            "is_first_party": False,
        },
        "screenshots": [
            {
                "url": s["src"],
                "locales": {
                    lang: {"caption": (s.get("caption") or {}).get(lang, "")}
                    for lang in ("ar", "en")
                },
            }
            for s in m.get("screenshots") or []
        ],
        "highlights": [],
        "features": [],
        "pricing": {
            "plan": pricing["model"],
            "locales": {lang: {"label": label[lang]} for lang in ("ar", "en")}
            if label
            else {},
            # app_billing reads these for a recurring price.
            **{
                k: pricing[k]
                for k in ("price_cents", "cycle", "currency")
                if pricing["model"] == "recurring"
            },
        },
        "languages": m.get("languages") or ["ar", "en"],
        "settings_schema": m.get("settings_schema") or [],
        "public_settings": [],
        "blocks": [],
        # The published contract, for the review diff and Phase 4 (OAuth).
        "app": {
            "app_url": m["app_url"],
            "embedded": m.get("embedded", False),
            "embedded_path": m.get("embedded_path"),
            "app_proxy": m.get("app_proxy"),
            "oauth": m["oauth"],
            "webhooks": m["webhooks"],
            "privacy_policy_url": dev.get("privacy_policy_url"),
            "terms_url": dev.get("terms_url"),
        },
    }


def validate_settings(
    values: dict[str, Any], schema: list[dict[str, Any]], *, strict_keys: bool
) -> dict[str, Any]:
    """Check merchant-entered app settings against the app's ``settings_schema``.

    Returns ``{id: message}`` for every bad value (empty = valid). Known ids
    are type-checked. Unknown keys are rejected for Partner Apps
    (``strict_keys``); first-party apps keep their legacy keys. Field types
    this function does not know are accepted as-is.
    """
    fields = {f["id"]: f for f in schema if isinstance(f, dict) and f.get("id")}
    errors: dict[str, str] = {}
    for key, value in values.items():
        field = fields.get(key)
        if field is None:
            if strict_keys:
                errors[key] = "not a setting of this app"
            continue
        kind = field.get("type")
        if kind == "checkbox" and not isinstance(value, bool):
            errors[key] = "must be true or false"
        elif kind in ("number", "range"):
            if isinstance(value, bool) or not isinstance(value, int | float):
                errors[key] = "must be a number"
            elif field.get("min") is not None and value < field["min"]:
                errors[key] = f"must be at least {field['min']}"
            elif field.get("max") is not None and value > field["max"]:
                errors[key] = f"must be at most {field['max']}"
        elif kind in ("select", "radio"):
            allowed = {str(o.get("value")) for o in field.get("options") or []}
            if str(value) not in allowed:
                errors[key] = "is not one of the options"
        elif kind in ("text", "textarea", "color"):
            if not isinstance(value, str):
                errors[key] = "must be text"
        elif kind == "url":
            if not isinstance(value, str) or (
                value and not value.startswith(("https://", "/"))
            ):
                errors[key] = "must be an https:// URL"
    return errors
