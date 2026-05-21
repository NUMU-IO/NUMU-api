"""Checkout field configuration.

Merchant-configurable checkout form. Lives in ``store.settings["checkout_fields"]``
(JSONB), no migration required. Storefront reads this to render the form,
backend reads it to validate required fields and accept custom field values.

Shape::

    {
      "standard_fields": {
        "first_name":   {"enabled": True, "required": True},
        "last_name":    {"enabled": True, "required": True},
        "phone":        {"enabled": True, "required": True},
        "email":        {"enabled": True, "required": False},
        "governorate":  {"enabled": True, "required": True},
        "area":         {"enabled": True, "required": True},
        "address":      {"enabled": True, "required": True},
        "landmark":     {"enabled": True, "required": False},
        "notes":        {"enabled": False, "required": False}
      },
      "custom_fields": [
        {
          "id": "<uuid>",
          "label": "Preferred delivery time",
          "label_ar": "موعد التسليم المفضل",
          "type": "text" | "textarea" | "number" | "select" | "checkbox",
          "required": false,
          "placeholder": "...",
          "options": ["Morning", "Afternoon"],   # select only
          "position": 0
        }
      ]
    }
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

SETTINGS_KEY = "checkout_fields"

STANDARD_FIELD_KEYS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "phone",
    "email",
    "governorate",
    "area",
    "address",
    "landmark",
    "notes",
)

# Fields we never let the merchant hide — orders literally can't ship without them.
LOCKED_ENABLED: frozenset[str] = frozenset({
    "first_name",
    "phone",
    "address",
    "governorate",
})

CUSTOM_FIELD_TYPES: tuple[str, ...] = (
    "text",
    "textarea",
    "number",
    "select",
    "checkbox",
)
MAX_CUSTOM_FIELDS = 10
MAX_LABEL_LEN = 80
MAX_PLACEHOLDER_LEN = 120
MAX_OPTION_LEN = 80
MAX_OPTIONS = 20
MAX_VALUE_LEN = 500


def default_config() -> dict[str, Any]:
    """Return the built-in default checkout config."""
    return {
        "standard_fields": {
            "first_name": {"enabled": True, "required": True},
            "last_name": {"enabled": True, "required": True},
            "phone": {"enabled": True, "required": True},
            "email": {"enabled": True, "required": False},
            "governorate": {"enabled": True, "required": True},
            "area": {"enabled": True, "required": True},
            "address": {"enabled": True, "required": True},
            "landmark": {"enabled": True, "required": False},
            "notes": {"enabled": False, "required": False},
        },
        "custom_fields": [],
    }


def resolve_config(store_settings: dict | None) -> dict[str, Any]:
    """Merge stored config over defaults so new fields get sensible defaults."""
    cfg = default_config()
    stored = (store_settings or {}).get(SETTINGS_KEY) or {}
    stored_std = stored.get("standard_fields") or {}
    for key, defaults in cfg["standard_fields"].items():
        override = stored_std.get(key) or {}
        enabled = bool(override.get("enabled", defaults["enabled"]))
        if key in LOCKED_ENABLED:
            enabled = True
        cfg["standard_fields"][key] = {
            "enabled": enabled,
            "required": bool(override.get("required", defaults["required"])),
        }
    raw_custom = stored.get("custom_fields") or []
    if isinstance(raw_custom, list):
        cfg["custom_fields"] = raw_custom[:MAX_CUSTOM_FIELDS]
    return cfg


# ── Pydantic ──────────────────────────────────────────────────────────────


class StandardFieldSetting(BaseModel):
    enabled: bool = True
    required: bool = False


class CustomFieldSetting(BaseModel):
    id: str | None = None
    label: str = Field(..., min_length=1, max_length=MAX_LABEL_LEN)
    label_ar: str | None = Field(None, max_length=MAX_LABEL_LEN)
    type: str = Field(..., description="text | textarea | number | select | checkbox")
    required: bool = False
    placeholder: str | None = Field(None, max_length=MAX_PLACEHOLDER_LEN)
    options: list[str] | None = None
    position: int = 0

    @field_validator("type")
    @classmethod
    def _type_allowed(cls, v: str) -> str:
        if v not in CUSTOM_FIELD_TYPES:
            raise ValueError(f"type must be one of {CUSTOM_FIELD_TYPES}")
        return v

    @field_validator("options")
    @classmethod
    def _clean_options(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > MAX_OPTIONS:
            raise ValueError(f"max {MAX_OPTIONS} options")
        cleaned = [str(o).strip()[:MAX_OPTION_LEN] for o in v if str(o).strip()]
        return cleaned or None


class CheckoutFieldsConfig(BaseModel):
    standard_fields: dict[str, StandardFieldSetting] = Field(default_factory=dict)
    custom_fields: list[CustomFieldSetting] = Field(default_factory=list)

    def to_storage(self) -> dict[str, Any]:
        """Serialize for persistence — applies locks, trims, assigns IDs, sorts."""
        std: dict[str, dict] = {}
        defaults = default_config()["standard_fields"]
        for key, default in defaults.items():
            sf = self.standard_fields.get(key)
            enabled = sf.enabled if sf else default["enabled"]
            required = sf.required if sf else default["required"]
            if key in LOCKED_ENABLED:
                enabled = True
            std[key] = {"enabled": bool(enabled), "required": bool(required)}

        custom: list[dict] = []
        seen_ids: set[str] = set()
        items = sorted(self.custom_fields, key=lambda f: f.position)[:MAX_CUSTOM_FIELDS]
        for idx, f in enumerate(items):
            fid = f.id if f.id and f.id not in seen_ids else str(uuid4())
            seen_ids.add(fid)
            entry = {
                "id": fid,
                "label": f.label.strip(),
                "label_ar": (f.label_ar or "").strip() or None,
                "type": f.type,
                "required": bool(f.required),
                "placeholder": (f.placeholder or "").strip() or None,
                "options": f.options if f.type == "select" else None,
                "position": idx,
            }
            custom.append(entry)

        return {"standard_fields": std, "custom_fields": custom}


def validate_custom_field_values(
    config: dict[str, Any],
    submitted: dict[str, Any] | None,
) -> tuple[list[dict], list[str]]:
    """Validate submitted custom field values against the store config.

    Returns ``(accepted, errors)`` — ``accepted`` is the list of
    ``{id, label, value}`` records to persist on the order; ``errors``
    is a list of field-level error messages (empty on success).
    """
    submitted = submitted or {}
    errors: list[str] = []
    accepted: list[dict] = []
    for f in config.get("custom_fields", []) or []:
        fid = f.get("id")
        if not fid:
            continue
        raw = submitted.get(fid)
        present = raw is not None and str(raw).strip() != ""
        if f.get("required") and not present:
            errors.append(f"{f.get('label') or fid} is required")
            continue
        if not present:
            continue

        value: Any
        ftype = f.get("type")
        if ftype == "checkbox":
            value = (
                bool(raw)
                if not isinstance(raw, str)
                else raw.lower() in ("1", "true", "yes", "on")
            )
        elif ftype == "number":
            try:
                value = float(raw)
            except (TypeError, ValueError):
                errors.append(f"{f.get('label') or fid} must be a number")
                continue
        elif ftype == "select":
            v = str(raw).strip()
            opts = f.get("options") or []
            if opts and v not in opts:
                errors.append(f"{f.get('label') or fid}: invalid option")
                continue
            value = v
        else:  # text / textarea
            value = str(raw).strip()[:MAX_VALUE_LEN]

        accepted.append({"id": fid, "label": f.get("label"), "value": value})
    return accepted, errors
