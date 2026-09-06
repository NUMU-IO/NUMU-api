"""Merchant-defined courier profiles — the Tier 3 carrier model.

Most couriers Egyptian merchants actually use have **no API**. البريد
المصري's e-commerce service (وصّلها) is a web portal; Cathedis, Sprint,
MCS, R2S, Apex, Xceed and Door To Door run on WhatsApp and spreadsheets.
There is nothing to integrate, which is why every platform in this market
files them under "and all local shipping companies".

A profile is what makes one of those usable anyway: a name, who it
delivers for, what it charges, and how to reach it. NUMU generates the
waybill and the tracking number; the courier just carries the parcel.

**This extends the `manual` flag that already exists**, it does not sit
beside it. ``shipping.manual.enabled`` defaults to **True on every
store**, including both live ones, so every store already has manual
shipping switched on. :func:`backfill_default_profile` is what carries
that forward — a store that was relying on the flag keeps working.

Profiles live in ``store.settings.shipping.manual.profiles`` rather than
a table: shipping zones and carrier credentials already live in settings,
and this needs no migration to reach stores that are already using it.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.
"""

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

MANUAL_SLUG = "manual"
_PROFILES_KEY = "profiles"

#: Egyptian governorate codes, as used by the shipping-zone resolver.
_GOV_CODE = re.compile(r"^EG-[A-Z]{1,4}$")


@dataclass
class ManualCarrierProfile:
    """One merchant-defined courier."""

    id: str
    name_en: str
    name_ar: str
    #: Governorates this courier delivers to. Empty = everywhere.
    governorate_codes: list[str] = field(default_factory=list)
    contact_phone: str | None = None
    contact_name: str | None = None
    #: Local time after which parcels go out the next day, e.g. "16:00".
    cutoff_time: str | None = None
    #: The courier's own tracking page, if it has one. Egypt Post does;
    #: a man with a motorbike does not.
    tracking_url_template: str | None = None
    notes: str | None = None
    is_active: bool = True
    #: Set on seeded profiles so the UI can show a known courier's card
    #: rather than a blank form.
    seed_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ManualCarrierProfile":
        """Build from stored JSON, tolerating a partially-written row.

        These live in free-form settings JSON, so a row can be missing a
        field an older or interrupted write never set. Defaulting the
        names keeps one bad row from taking down the whole courier list —
        the merchant sees a profile to fix rather than an error page.
        """
        known = set(cls.__dataclass_fields__)
        values = {k: v for k, v in (raw or {}).items() if k in known}
        values.setdefault("id", "")
        name = values.get("name_en") or values.get("name_ar") or "Unnamed courier"
        values["name_en"] = values.get("name_en") or name
        values["name_ar"] = values.get("name_ar") or name
        return cls(**values)

    def name(self, lang: str = "en") -> str:
        return self.name_ar if lang == "ar" else self.name_en

    def covers(self, governorate_code: str | None) -> bool:
        """Whether this courier delivers to a governorate.

        An empty coverage list means "everywhere" — a merchant who hasn't
        said otherwise should not have parcels silently refused.
        """
        if not self.governorate_codes:
            return True
        if not governorate_code:
            return True
        return governorate_code.upper() in {c.upper() for c in self.governorate_codes}

    def tracking_url(self, tracking_number: str | None) -> str | None:
        if not tracking_number or not self.tracking_url_template:
            return None
        return self.tracking_url_template.format(tracking_number=tracking_number)


class ProfileValidationError(ValueError):
    """Raised when a submitted profile cannot be stored as given."""


def _manual_block(store_settings: dict | None) -> dict[str, Any]:
    block = (store_settings or {}).get("shipping", {})
    if not isinstance(block, dict):
        return {}
    entry = block.get(MANUAL_SLUG, {})
    return entry if isinstance(entry, dict) else {}


def list_profiles(store_settings: dict | None) -> list[ManualCarrierProfile]:
    """Every courier profile on this store, active or not."""
    raw = _manual_block(store_settings).get(_PROFILES_KEY) or []
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("id"):
            out.append(ManualCarrierProfile.from_dict(entry))
    return out


def active_profiles(store_settings: dict | None) -> list[ManualCarrierProfile]:
    return [p for p in list_profiles(store_settings) if p.is_active]


def get_profile(
    store_settings: dict | None, profile_id: str
) -> ManualCarrierProfile | None:
    for profile in list_profiles(store_settings):
        if profile.id == profile_id:
            return profile
    return None


def profiles_for_governorate(
    store_settings: dict | None, governorate_code: str | None
) -> list[ManualCarrierProfile]:
    return [p for p in active_profiles(store_settings) if p.covers(governorate_code)]


def validate_profile(values: dict[str, Any]) -> dict[str, Any]:
    """Clean a submitted profile, or raise with a specific reason."""
    name_en = (values.get("name_en") or "").strip()
    name_ar = (values.get("name_ar") or "").strip()
    if not name_en and not name_ar:
        raise ProfileValidationError("A courier needs a name.")
    # A merchant who fills only one language should not end up with a
    # blank card in the other.
    name_en = name_en or name_ar
    name_ar = name_ar or name_en

    codes: list[str] = []
    for code in values.get("governorate_codes") or []:
        code = str(code).strip().upper()
        if not _GOV_CODE.match(code):
            raise ProfileValidationError(f"'{code}' is not a governorate code.")
        if code not in codes:
            codes.append(code)

    template = (values.get("tracking_url_template") or "").strip() or None
    if template:
        if not template.startswith("https://"):
            raise ProfileValidationError("A tracking link must start with https://.")
        if "{tracking_number}" not in template:
            raise ProfileValidationError(
                "A tracking link needs {tracking_number} in it, or every "
                "parcel gets the same URL."
            )

    cutoff = (values.get("cutoff_time") or "").strip() or None
    if cutoff and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", cutoff):
        raise ProfileValidationError("Cut-off time must look like 16:00.")

    return {
        "name_en": name_en[:120],
        "name_ar": name_ar[:120],
        "governorate_codes": codes,
        "contact_phone": (values.get("contact_phone") or "").strip() or None,
        "contact_name": (values.get("contact_name") or "").strip() or None,
        "cutoff_time": cutoff,
        "tracking_url_template": template,
        "notes": (values.get("notes") or "").strip() or None,
        "is_active": bool(values.get("is_active", True)),
        "seed_key": values.get("seed_key") or None,
    }


def _write_profiles(
    store_settings: dict | None, profiles: list[ManualCarrierProfile]
) -> dict[str, Any]:
    settings = dict(store_settings or {})
    shipping = dict(settings.get("shipping", {}))
    manual = dict(shipping.get(MANUAL_SLUG, {}))
    manual[_PROFILES_KEY] = [p.to_dict() for p in profiles]
    # A store with a courier configured is using manual shipping.
    manual.setdefault("is_configured", True)
    shipping[MANUAL_SLUG] = manual
    settings["shipping"] = shipping
    return settings


def upsert_profile(
    store_settings: dict | None,
    values: dict[str, Any],
    profile_id: str | None = None,
) -> tuple[dict[str, Any], ManualCarrierProfile]:
    """Create or update a profile. Returns (new settings, profile)."""
    cleaned = validate_profile(values)
    profiles = list_profiles(store_settings)

    if profile_id:
        for index, existing in enumerate(profiles):
            if existing.id == profile_id:
                profiles[index] = ManualCarrierProfile(id=profile_id, **cleaned)
                return _write_profiles(store_settings, profiles), profiles[index]
        raise ProfileValidationError(f"No courier with id '{profile_id}'.")

    profile = ManualCarrierProfile(id=str(uuid.uuid4()), **cleaned)
    profiles.append(profile)
    return _write_profiles(store_settings, profiles), profile


def delete_profile(store_settings: dict | None, profile_id: str) -> dict[str, Any]:
    """Remove a profile.

    Existing shipments keep their own copy of the courier's name, so
    deleting a profile never orphans a parcel already in transit.
    """
    remaining = [p for p in list_profiles(store_settings) if p.id != profile_id]
    return _write_profiles(store_settings, remaining)


def backfill_default_profile(store_settings: dict | None) -> dict[str, Any] | None:
    """Give a store already using the `manual` flag one default profile.

    ``shipping.manual.enabled`` ships **True by default**, so every
    existing store — including both live ones — has manual shipping on
    without ever having defined a courier. Without this they would open
    the new UI to an empty list and lose a working setup.

    Returns updated settings, or **None when nothing needs doing**, so
    callers can skip the write. Safe to run repeatedly.
    """
    manual = _manual_block(store_settings)
    if not manual.get("enabled"):
        return None
    if list_profiles(store_settings):
        return None

    settings, _ = upsert_profile(
        store_settings,
        {
            "name_en": "My courier",
            "name_ar": "المندوب بتاعي",
            "is_active": True,
            "notes": (
                "Created automatically from your existing manual shipping "
                "setting. Rename it or add the courier's details."
            ),
        },
    )
    return settings


__all__ = [
    "MANUAL_SLUG",
    "ManualCarrierProfile",
    "ProfileValidationError",
    "active_profiles",
    "backfill_default_profile",
    "delete_profile",
    "get_profile",
    "list_profiles",
    "profiles_for_governorate",
    "upsert_profile",
    "validate_profile",
]
