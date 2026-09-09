"""Known Egyptian couriers, pre-filled so a merchant isn't given a blank form.

These are the Tier 3 couriers from the shipping plan — the ones with no
API to integrate. Seeding them means a merchant picks their courier and
fills in only their own rates, instead of typing a name and 27
governorate codes from scratch.

**The honesty problem.** Real coverage, contact numbers and cut-off times
are operational facts nobody can invent. So each seed carries
``data_verified``:

* ``True``  — coverage and operating details checked with the courier.
* ``False`` — a **starting point**: full 27-governorate coverage, shown
  in the hub as unconfirmed for the merchant to correct.

A contact number is tracked separately by ``contact_source``, because the
two are different claims: Egypt Post's published call centre is a fact
even while its governorate coverage is not. **A phone without a stated
source is not allowed** — a wrong courier number is worse than none.

Full coverage is the right default for an unverified seed. A courier that
actually covers less will have parcels declined by the courier, which the
merchant sees immediately; a courier wrongly limited to three
governorates silently hides deliveries the merchant could have made, and
nobody finds out.

Nothing here blocks on getting the data — an unverified seed is still far
better than an empty form, and correcting one is a text field.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.4.
"""

from dataclasses import dataclass
from typing import Any


def all_governorate_codes() -> list[str]:
    """Every Egyptian governorate code, from the canonical list."""
    from src.infrastructure.external_services.bosta.governorates import (
        EGYPTIAN_GOVERNORATES,
    )

    return [g.code for g in EGYPTIAN_GOVERNORATES]


@dataclass(frozen=True)
class CourierSeed:
    """A known courier a merchant can start from."""

    key: str
    name_en: str
    name_ar: str
    #: Empty means "everywhere" — filled from the canonical list at use
    #: time so an unverified seed is explicit rather than silently blank.
    governorate_codes: tuple[str, ...] = ()
    contact_phone: str | None = None
    #: Where ``contact_phone`` came from. **Required whenever a phone is
    #: set** — a courier contact that is wrong is worse than one that is
    #: absent, so anyone adding a number has to say where they got it.
    contact_source: str = ""
    tracking_url_template: str | None = None
    #: Whether *coverage and operating details* have been confirmed with
    #: the courier. Independent of ``contact_source``: Egypt Post's
    #: published call centre is a fact, its governorate coverage is not.
    data_verified: bool = False
    note_en: str = ""
    note_ar: str = ""

    def to_profile_values(self) -> dict[str, Any]:
        """Shape this seed as a courier-profile payload."""
        return {
            "name_en": self.name_en,
            "name_ar": self.name_ar,
            "governorate_codes": list(self.governorate_codes)
            or all_governorate_codes(),
            "contact_phone": self.contact_phone,
            "tracking_url_template": self.tracking_url_template,
            "notes": self.note_ar or self.note_en or None,
            "is_active": True,
            "seed_key": self.key,
        }

    def as_dict(self) -> dict[str, Any]:
        """For the hub's "pick your courier" list."""
        return {
            "key": self.key,
            "name_en": self.name_en,
            "name_ar": self.name_ar,
            "contact_phone": self.contact_phone,
            "data_verified": self.data_verified,
            "covers_all_governorates": not self.governorate_codes,
            "note_en": self.note_en,
            "note_ar": self.note_ar,
        }


#: The couriers named in the plan. Every one of these has **no API** —
#: that is why they are here rather than in the carrier registry.
#:
#: All are currently ``data_verified=False``. Correcting one is a matter
#: of filling in coverage, phone and cut-off; the structure does not
#: change, so this list can be firmed up without touching any other code.
COURIER_SEEDS: tuple[CourierSeed, ...] = (
    CourierSeed(
        key="egypt_post",
        name_en="Egypt Post",
        name_ar="البريد المصري",
        # 16789 is Egypt Post's published national call centre, quoted in
        # its own e-commerce ("وصّلها") material.
        contact_phone="16789",
        contact_source="Egypt Post's published national call centre.",
        note_en=(
            "Egypt Post's e-commerce service (Wassalha) is a merchant web "
            "portal, not an API — register there, then track parcels here."
        ),
        note_ar=(
            "خدمة «وصّلها» من البريد المصري بتشتغل من موقعهم مش عن طريق API — "
            "سجّل عندهم، وتابع الشحنات من هنا."
        ),
    ),
    CourierSeed(key="cathedis", name_en="Cathedis", name_ar="كاثيدس"),
    CourierSeed(key="sprint", name_en="Sprint Logistics", name_ar="سبرينت"),
    CourierSeed(key="mcs", name_en="MCS Courier", name_ar="إم سي إس"),
    CourierSeed(key="r2s", name_en="R2S Logistics", name_ar="آر تو إس"),
    CourierSeed(key="apex", name_en="Apex Delivery", name_ar="أبيكس"),
    CourierSeed(key="xceed", name_en="Xceed Courier", name_ar="إكسيد"),
    CourierSeed(key="door_to_door", name_en="Door To Door Egypt", name_ar="دور تو دور"),
    # ── Companies a merchant can hand a sheet to today ───────────────
    #
    # Added 2026-09-09 at Yousef's request. None of the four publishes an
    # API, so they are Tier 3 by the same rule as the rest of this file:
    # NUMU issues the waybill and the tracking number, the merchant sends
    # the company a CSV of the day's parcels, and status comes back
    # through the CSV import.
    #
    # Details are what each company publishes on its own site, read
    # 2026-09-09. Arabic names are transliterations except Waselha
    # (وصلها) and Barashout (براشوت), which the companies write
    # themselves. Coverage is unverified for all four, so each ships with
    # the full 27 governorates and is labelled unconfirmed in the hub.
    CourierSeed(
        key="waselha",
        name_en="Waselha",
        name_ar="وصلها",
        note_en=(
            "A platform that matches merchants with couriers, with its own "
            "tracking and settlement. Cairo-based, no public API."
        ),
        note_ar=(
            "منصة بتوصّل التجار بالمندوبين، وعندها تتبّع وتسويات خاصة بيها. "
            "مقرها القاهرة، ومفيش API."
        ),
    ),
    CourierSeed(
        key="flextock",
        name_en="Flextock",
        name_ar="فليكستوك",
        note_en=(
            "Fulfilment as well as delivery — they can hold your stock and "
            "ship from their warehouse. Operates in Egypt, Saudi Arabia "
            "and the UAE."
        ),
        note_ar=(
            "بيعملوا تخزين وشحن مع بعض — ممكن يمسكوا بضاعتك ويشحنوا من "
            "مخزنهم. شغالين في مصر والسعودية والإمارات."
        ),
    ),
    CourierSeed(
        key="holyship",
        name_en="Holy Ship",
        name_ar="هولي شيب",
        note_en="Last-mile delivery and warehousing for online stores.",
        note_ar="توصيل الميل الأخير وتخزين للمتاجر الأونلاين.",
    ),
    CourierSeed(
        key="barashout",
        name_en="Barashout",
        name_ar="براشوت",
        contact_phone="+201099541922",
        contact_source="Published on barashout.com's contact page, read 2026-09-09.",
        note_en=(
            "Same-day inside a governorate, and they handle fragile goods. "
            "Based in Mansoura, delivering nationwide through partners."
        ),
        note_ar=(
            "توصيل في نفس اليوم جوه المحافظة، وبيشيلوا البضاعة القابلة "
            "للكسر. مقرهم المنصورة وبيوصّلوا كل مصر عن طريق شركاء."
        ),
    ),
    CourierSeed(
        key="own_courier",
        name_en="My own courier",
        name_ar="مندوب خاص",
        note_en="Your own rider or a local courier you arrange yourself.",
        note_ar="مندوبك أنت أو شركة محلية بتتفق معاها بنفسك.",
        # Not a company, so nothing to verify — this one is honest as-is.
        data_verified=True,
    ),
)

for _seed in COURIER_SEEDS:
    if _seed.contact_phone and not _seed.contact_source:
        raise AssertionError(
            f"Courier seed '{_seed.key}' has a phone number with no stated "
            f"source. Say where it came from, or leave it out."
        )

_BY_KEY = {seed.key: seed for seed in COURIER_SEEDS}


def list_seeds() -> list[CourierSeed]:
    return list(COURIER_SEEDS)


def get_seed(key: str) -> CourierSeed | None:
    return _BY_KEY.get((key or "").strip().lower())


def seed_catalog() -> list[dict[str, Any]]:
    """The hub's "pick your courier" list."""
    return [seed.as_dict() for seed in COURIER_SEEDS]


def unverified_keys() -> list[str]:
    """Seeds still carrying placeholder coverage.

    Surfaced so the gap is visible rather than quietly shipped as fact.
    """
    return [s.key for s in COURIER_SEEDS if not s.data_verified]


__all__ = [
    "COURIER_SEEDS",
    "CourierSeed",
    "all_governorate_codes",
    "get_seed",
    "list_seeds",
    "seed_catalog",
    "unverified_keys",
]
