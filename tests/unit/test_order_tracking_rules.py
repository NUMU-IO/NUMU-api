"""Order Tracking rules, no I/O (TRK-U-01 … U-07, C-04, C-05, C-06, R-03)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.application.services import order_tracking_service as svc
from src.core.utils.store_timezone import local_date

CAIRO = "Africa/Cairo"
PHONE = "01098433918"


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


#: A shipment's history with what TESTS.md asks for: a repeated in_transit,
#: an unknown carrier scan, a failure whose description carries a phone, then
#: a second out-for-delivery.
HISTORY = [
    {"from": "created", "to": "picked_up", "description": "Picked up by carrier",
     "timestamp": "2026-10-16T08:00:00+00:00"},
    {"from": "picked_up", "to": "in_transit", "description": "Hub A",
     "timestamp": "2026-10-16T12:00:00+00:00"},
    {"from": "in_transit", "to": "in_transit", "description": "Hub B",
     "timestamp": "2026-10-17T07:00:00+00:00"},
    {"from": "in_transit", "to": "weird_scan", "description": "?",
     "timestamp": "2026-10-17T08:00:00+00:00"},
    {"from": "weird_scan", "to": "out_for_delivery", "description": "Out",
     "timestamp": "2026-10-17T09:00:00+00:00"},
    {"from": "out_for_delivery", "to": "failed",
     "description": f"Delivery failed: customer {PHONE} unreachable",
     "timestamp": "2026-10-17T15:00:00+00:00"},
    {"from": "failed", "to": "failed", "description": "re-sent webhook",
     "timestamp": "2026-10-17T15:05:00+00:00"},
    {"from": "failed", "to": "out_for_delivery", "description": "Out again",
     "timestamp": "2026-10-18T09:00:00+00:00"},
]  # fmt: skip
CONFIRMED = _at("2026-10-15T10:00:00")


# ─── TRK-U-01: steps ──────────────────────────────────────────────────


def test_the_journey_collapses_repeats_and_drops_unknowns_and_descriptions():
    steps = svc.merchant_steps("shipped", CONFIRMED, HISTORY)
    assert [s["key"] for s in steps] == [
        "confirmed",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "failed",
        "out_for_delivery",
    ]
    assert steps[2]["at"] == "2026-10-16T12:00:00+00:00"  # the first of a run
    assert all(set(s) == {"key", "at"} for s in steps)
    text = json.dumps(steps)
    assert PHONE not in text and "Hub" not in text and "weird" not in text


def test_a_pending_order_has_no_confirmed_step():
    assert svc.merchant_steps("pending", None, []) == []
    assert svc.merchant_steps("confirmed", None, [])[0] == {
        "key": "confirmed",
        "at": None,
    }


def _states(steps):
    return [(s["key"], s["state"]) for s in steps]


def test_the_shoppers_five_steps():
    steps = svc.shopper_steps("out_for_delivery", CONFIRMED, HISTORY)
    assert _states(steps) == [
        ("confirmed", "done"),
        ("picked_up", "done"),
        ("in_transit", "done"),
        ("out_for_delivery", "current"),
        ("delivered", "ahead"),
    ]
    assert steps[3]["at"] == "2026-10-18T09:00:00+00:00"  # the second attempt
    assert steps[4]["at"] is None
    assert PHONE not in json.dumps(steps)


@pytest.mark.parametrize(
    ("status", "want"),
    [
        ("pending", [("confirmed", "current")] + [(k, "ahead") for k in svc.LINE[1:]]),
        (
            "processing",
            [("confirmed", "current")] + [(k, "ahead") for k in svc.LINE[1:]],
        ),
        (
            "failed",
            [
                ("confirmed", "done"),
                ("picked_up", "done"),
                ("in_transit", "done"),
                ("failed", "current"),
                ("delivered", "ahead"),
            ],
        ),
        ("delivered", [(k, "done") for k in svc.LINE[:4]] + [("delivered", "current")]),
    ],
)
def test_where_the_current_step_is(status, want):
    assert _states(svc.shopper_steps(status, CONFIRMED, HISTORY)) == want


def test_returned_and_cancelled_follow_the_last_step_reached():
    returned = HISTORY + [
        {"from": "out_for_delivery", "to": "returned", "description": "x",
         "timestamp": "2026-10-19T09:00:00+00:00"}
    ]  # fmt: skip
    assert _states(svc.shopper_steps("returned", CONFIRMED, returned)) == [
        ("confirmed", "done"),
        ("picked_up", "done"),
        ("in_transit", "done"),
        ("out_for_delivery", "done"),
        ("returned", "current"),
        ("delivered", "ahead"),
    ]
    cancelled = svc.shopper_steps("cancelled", None, [])
    assert _states(cancelled)[0] == ("cancelled", "current")
    assert [k for k, _s in _states(cancelled)[1:]] == list(svc.LINE)


def test_attempts_count_moves_into_a_status_not_repeats():
    assert svc.entries(HISTORY, "failed") == 1  # failed→failed is a re-sent webhook
    assert svc.entries(HISTORY, "out_for_delivery") == 2
    assert svc.entered_at(HISTORY, "out_for_delivery") == _at("2026-10-18T09:00:00")
    assert svc.entries(None, "failed") == 0 and svc.entered_at([], "failed") is None


@pytest.mark.parametrize(
    ("shipment", "order", "want"),
    [
        ("out_for_delivery", "shipped", "out_for_delivery"),
        (None, "processing", "processing"),
        ("created", "shipped", "in_transit"),  # booked, the order says it left
        ("picked_up", "processing", "picked_up"),
        ("in_transit", "delivered", "delivered"),  # the merchant closed it
        ("failed", "cancelled", "cancelled"),
        (None, "refunded", "cancelled"),
        ("returned", "shipped", "returned"),
    ],
)
def test_parcel_status(shipment, order, want):
    assert svc.parcel_status(shipment, order) == want


# ─── TRK-U-02 / U-04: window and area ─────────────────────────────────


@pytest.mark.parametrize(
    ("placed", "tz", "days", "want"),
    [
        # 23:59 Cairo (summer time, UTC+3): still the 18th
        ("2026-10-18T20:59:00", CAIRO, (2, 4), ("2026-10-20", "2026-10-22")),
        # 00:30 Cairo: the 19th
        ("2026-10-18T21:30:00", CAIRO, (2, 4), ("2026-10-21", "2026-10-23")),
        # no zone on the order: 2–5 days
        ("2026-10-18T10:00:00", CAIRO, None, ("2026-10-20", "2026-10-23")),
        # a zone saved with max < min keeps a one-day window
        ("2026-10-18T10:00:00", CAIRO, (3, 1), ("2026-10-21", "2026-10-21")),
        # a Saudi store's day
        ("2026-10-18T21:30:00", "Asia/Riyadh", (1, 2), ("2026-10-20", "2026-10-21")),
        # winter (UTC+2): 23:30 Cairo is still the 1st
        ("2026-12-01T21:30:00", CAIRO, (1, 1), ("2026-12-02", "2026-12-02")),
    ],
)
def test_the_window_from_the_order_day(placed, tz, days, want):
    low, high = svc.eta_window(_at(placed), tz, days)
    assert (low.isoformat(), high.isoformat()) == want


@pytest.mark.parametrize(
    ("address", "want"),
    [
        ({"state": "القاهرة", "city": "مدينة نصر"}, ("EG-C", "مدينة نصر")),
        ({"state": "Cairo", "city": "Nasr City"}, ("EG-C", "Nasr City")),
        ({"state": "EG-C"}, ("EG-C", None)),
        ({"state": "CAI"}, ("EG-C", None)),
        ({"governorate": "الدقهلية", "city": "المنصورة"}, ("EG-DK", "المنصورة")),
        ({"city": "Mansoura"}, ("EG-DK", "Mansoura")),  # an alias in the city field
        ({"city": "قرية مش معروفة"}, (None, "قرية مش معروفة")),
        ({}, (None, None)),
        (None, (None, None)),
    ],
)
def test_the_area_never_the_street(address, want):
    assert svc.area_of(address) == want


def test_area_out():
    assert svc.area_out("EG-C", "مدينة نصر") == {
        "governorate_code": "EG-C",
        "governorate_ar": "القاهرة",
        "governorate_en": "Cairo",
        "city": "مدينة نصر",
    }
    assert svc.area_out(None, None) is None


def test_carrier_names_come_from_the_registry():
    assert svc.carrier_out("bosta") == {
        "key": "bosta",
        "name_ar": "بوسطة",
        "name_en": "Bosta",
    }
    assert svc.carrier_out("app:fast-courier")["name_en"] == "app:fast-courier"
    assert svc.carrier_out(None) is None


# ─── TRK-U-03: delayed ────────────────────────────────────────────────


def _today(utc: str, tz: str = CAIRO) -> date:
    return local_date(_at(utc), tz)


@pytest.mark.parametrize(
    ("now", "eta_max", "delayed"),
    [
        ("2026-07-15T20:59:00", "2026-07-15", False),  # 23:59 summer
        ("2026-07-15T21:00:00", "2026-07-15", True),  # 00:00 summer
        ("2026-12-01T21:59:00", "2026-12-01", False),  # 23:59 winter
        ("2026-12-01T22:00:00", "2026-12-01", True),  # 00:00 winter
        # The night summer time ends (29 Oct): 23:59 happens twice.
        ("2026-10-29T20:59:00", "2026-10-29", False),
        ("2026-10-29T21:59:00", "2026-10-29", False),
        ("2026-10-29T22:00:00", "2026-10-29", True),
        # The night it starts (24 Apr): 23:59 jumps to 01:00.
        ("2026-04-23T21:59:00", "2026-04-23", False),
        ("2026-04-23T22:00:00", "2026-04-23", True),
    ],
)
def test_delayed_turns_at_store_midnight(now, eta_max, delayed):
    today = _today(now)
    assert svc.is_delayed("in_transit", date.fromisoformat(eta_max), today) is delayed


@pytest.mark.parametrize("status", ["delivered", "returned", "cancelled"])
def test_a_closed_parcel_is_never_late(status):
    assert not svc.is_delayed(status, date(2026, 1, 1), date(2026, 10, 18))


def test_a_failed_parcel_can_be_late_and_no_window_never_is():
    assert svc.is_delayed("failed", date(2026, 10, 17), date(2026, 10, 18))
    assert not svc.is_delayed("in_transit", None, date(2026, 10, 18))


# ─── TRK-U-05: quiet hours ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "tz", "want"),
    [
        ("2026-10-18T18:59:00", CAIRO, "2026-10-18T18:59:00"),  # 21:59 → now
        ("2026-10-18T19:00:00", CAIRO, "2026-10-19T06:00:00"),  # 22:00 → 09:00
        ("2026-10-18T20:30:00", CAIRO, "2026-10-19T06:00:00"),  # 23:30 → 09:00
        ("2026-10-19T05:59:00", CAIRO, "2026-10-19T06:00:00"),  # 08:59 → 09:00
        ("2026-10-19T06:00:00", CAIRO, "2026-10-19T06:00:00"),  # 09:00 → now
        ("2026-12-01T20:00:00", CAIRO, "2026-12-02T07:00:00"),  # winter 22:00
        # 23:30 on the night summer time ends: 09:00 is UTC+2 again
        ("2026-10-29T20:30:00", CAIRO, "2026-10-30T07:00:00"),
        # A Saudi store's own clock: 22:30 Riyadh → 09:00 Riyadh
        ("2026-10-18T19:30:00", "Asia/Riyadh", "2026-10-19T06:00:00"),
    ],
)
def test_quiet_hours(now, tz, want):
    assert svc.send_time(_at(now), tz, spread_seconds=0) == _at(want)


def test_the_0900_release_is_spread():
    nine = _at("2026-10-19T06:00:00")
    got = svc.send_time(_at("2026-10-18T20:30:00"), CAIRO, rand=lambda: 0.5)
    assert got == nine + timedelta(seconds=svc.QUIET_SPREAD_SECONDS / 2)
    assert (
        nine
        <= svc.send_time(_at("2026-10-18T20:30:00"), CAIRO)
        < nine + timedelta(minutes=30)
    )


# ─── TRK-U-06: analytics ──────────────────────────────────────────────


def _parcel(status, *, gov="EG-C", carrier="bosta", days=None, attempts=0):
    shipped = _at("2026-10-01T10:00:00")
    return svc.Parcel(
        order_id=object(),
        order_number="1",
        status=status,
        carrier=carrier,
        governorate_code=gov,
        city=None,
        eta_min=None,
        eta_max=None,
        attempts=attempts,
        failed_at=None,
        shipped_at=shipped,
        delivered_at=shipped + timedelta(days=days) if days is not None else None,
        updated_at=shipped,
    )


def test_analytics_figures_by_hand():
    cairo = (
        [_parcel("delivered", days=2) for _ in range(8)]
        + [_parcel("delivered", days=4, attempts=1)]
        + [_parcel("delivered", days=3)]
        + [_parcel("returned", attempts=2)]
        + [_parcel("in_transit")]
    )  # 12 parcels: 10 delivered, 1 returned
    alex = [_parcel("delivered", gov="EG-ALX", carrier="manual", days=5)] * 3
    out = svc.analytics(cairo + alex)

    assert out["days"] == 30 and out["min_parcels"] == 10
    # (8×2 + 4 + 3 + 3×5) / 13 = 38 / 13 = 2.92…
    assert out["avg_delivery_days"] == 2.9
    assert out["success_rate"] == round(13 / 14, 2)  # 13 delivered, 1 returned
    assert out["failed_attempt_rate"] == round(3 / (3 + 13), 2)  # 3 failures
    assert out["areas"] == [
        {
            "area": svc.area_out("EG-C", None),
            "parcels": 12,
            "delivered_rate": round(10 / 12, 2),
            "returned_rate": round(1 / 12, 2),
            "avg_days": round((8 * 2 + 4 + 3) / 10, 1),
        }
    ]  # Alexandria has 3 parcels: not ranked
    assert [c["carrier"]["key"] for c in out["carriers"]] == ["bosta"]
    assert out["carriers"][0]["delivered_rate"] == round(10 / 12, 2)


def test_areas_worst_first_carriers_best_first():
    good = [_parcel("delivered", gov="EG-C", carrier="bosta", days=1)] * 10
    bad = [_parcel("returned", gov="EG-AST", carrier="mylerz")] * 5 + [
        _parcel("delivered", gov="EG-AST", carrier="mylerz", days=6)
    ] * 5
    out = svc.analytics(good + bad)
    assert [a["area"]["governorate_code"] for a in out["areas"]] == ["EG-AST", "EG-C"]
    assert [c["carrier"]["key"] for c in out["carriers"]] == ["bosta", "mylerz"]


def test_no_data_means_null_not_zero():
    out = svc.analytics([])
    assert out["avg_delivery_days"] is None
    assert out["success_rate"] is None and out["failed_attempt_rate"] is None
    assert out["areas"] == [] and out["carriers"] == []


# ─── TRK-U-07: the retry body ─────────────────────────────────────────


def _retry(**body):
    from src.api.v1.routes.storefront.apps_order_tracking import RetryIn

    return RetryIn(**{"day": "2026-10-19", "window": "afternoon", **body})


@pytest.mark.parametrize(
    "body",
    [
        {"window": "night"},
        {"note": "x" * 201},
        {"note": "جنب\nالصيدلية"},
        {"note": "a\x00b"},
        {"day": "tomorrow"},
        {"extra": 1},
    ],
)
def test_bad_bodies_are_refused_by_the_model(body):
    with pytest.raises(ValidationError):
        _retry(**body)


def test_good_bodies():
    assert _retry(note="  جنب الصيدلية  ").note == "جنب الصيدلية"
    assert _retry(note="ن" * 200).note == "ن" * 200  # 200 Arabic characters
    assert _retry(note="   ").note is None
    assert _retry().note is None
    # Stored as text; the front and the hub render it as text (TRK-S-13).
    assert _retry(note="<script>alert(1)</script>").note == "<script>alert(1)</script>"


def test_the_day_is_one_of_the_next_three():
    today = date(2026, 10, 18)
    assert svc.retry_day_ok(today, today)
    assert svc.retry_day_ok(today + timedelta(days=2), today)
    assert not svc.retry_day_ok(today + timedelta(days=3), today)
    assert not svc.retry_day_ok(today - timedelta(days=1), today)


def test_the_comment_and_the_courier_note():
    day = date(2026, 10, 19)
    comment = svc.retry_comment(day, "afternoon", "جنب الصيدلية")
    assert "عايز محاولة توصيل تانية" in comment and "afternoon (12–5)" in comment
    assert comment.endswith("ملاحظة / Note: جنب الصيدلية")
    assert svc.courier_note(None, day, "morning", None) == (
        "العميل عايز يستلم يوم 2026-10-19 الصبح (9–12)"
    )
    # Appended to what the shipment's note already said, never replacing it.
    assert svc.courier_note("Fragile", day, "evening", "الدور التالت") == (
        "Fragile\nالعميل عايز يستلم يوم 2026-10-19 بالليل (5–9): الدور التالت"
    )


# ─── TRK-C-04 / C-05: the fields the app reads ────────────────────────


def test_the_event_fields_the_handler_reads_exist():
    from src.core.events.commerce_events import (
        ShipmentCreatedEvent,
        ShipmentStatusChangedEvent,
    )
    from src.core.events.order_events import OrderStatusChangedEvent

    assert {"store_id", "order_id", "shipment_type"} <= set(
        ShipmentCreatedEvent.model_fields
    )
    assert {
        "store_id",
        "order_id",
        "shipment_id",
        "shipment_type",
        "status",
        "previous_status",
    } <= set(ShipmentStatusChangedEvent.model_fields)
    assert {"store_id", "order_id", "new_status"} <= set(
        OrderStatusChangedEvent.model_fields
    )


def test_the_zone_fields_the_window_reads_exist():
    from src.api.v1.routes.storefront.shipping import ShippingOptionResponse
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.shipping_zone import (
        ShippingZoneModel,
    )

    assert {"estimated_days_min", "estimated_days_max"} <= set(
        ShippingOptionResponse.model_fields
    )
    assert hasattr(ShippingZoneModel, "estimated_days_min")
    assert hasattr(ShippingZoneModel, "estimated_days_max")
    assert hasattr(OrderModel, "shipping_zone_id")


# ─── TRK-C-06 / R-03: the two templates ───────────────────────────────

NAMES = {
    svc.OUT_FOR_DELIVERY: "app_order_tracking_out_for_delivery_v1",
    svc.DELIVERY_FAILED: "app_order_tracking_delivery_failed_v1",
}


def test_the_two_templates():
    from src.core.interfaces.services.messaging_service import (
        EGYPTIAN_TEMPLATES,
        MessageType,
    )
    from src.core.whatsapp_rich_templates import RICH_TEMPLATES

    for kind, name in NAMES.items():
        assert svc.KINDS[kind][1] == name
        entries = [t for t in RICH_TEMPLATES if t["name"] == name]
        assert sorted(t["language"] for t in entries) == ["ar", "en"]
        for t in entries:
            assert t["category"] == "UTILITY"
            assert [b["type"] for b in t["buttons"]] == ["URL"]  # no quick reply
            assert t["buttons"][0]["url"] == "https://numueg.app/o/{{1}}"
            assert t["body"].count("{{") == 3 and len(t["body_examples"]) == 3
        for lang in ("ar", "en"):
            mapped = EGYPTIAN_TEMPLATES[MessageType(kind)][lang]
            assert (mapped.name, mapped.language) == (name, lang)
            params = [p for c in mapped.components for p in c["parameters"]]
            # Four parameters: three in the body, the link on the button.
            assert params == ["customer_name", "order_number", "store_name", "order_id"]


def test_the_backfill_and_the_seed_cover_both():
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic/versions/20261018_order_tracking_templates.py"
    )
    spec = importlib.util.spec_from_file_location("trk_templates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.NAMES) == set(NAMES.values())
    assert module.down_revision == "order_tracking_20261018"


def test_gowa_may_send_both_and_the_dead_path_is_gone():
    from src.core.interfaces.services.messaging_service import MessageType
    from src.core.whatsapp_plain_render import _PLAIN_ONLY_TEMPLATES
    from src.infrastructure.external_services.whatsapp.gowa_guard import (
        DEFAULT_ALLOWED_TYPES,
    )
    from src.infrastructure.external_services.whatsapp.messaging_service import (
        WhatsAppMessagingService,
    )

    assert {str(MessageType.OUT_FOR_DELIVERY), str(MessageType.DELIVERY_FAILED)} <= (
        DEFAULT_ALLOWED_TYPES
    )
    assert not hasattr(WhatsAppMessagingService, "send_out_for_delivery")
    assert not any(
        t["name"].startswith("out_for_delivery") for t in _PLAIN_ONLY_TEMPLATES
    )


@pytest.mark.parametrize("lang", ["ar", "en"])
@pytest.mark.parametrize("kind", [svc.OUT_FOR_DELIVERY, svc.DELIVERY_FAILED])
def test_both_render_with_sample_values(kind, lang):
    """TRK-R-03: what a GOWA store sends; numbers and the link unbroken."""
    from src.core.interfaces.services.messaging_service import MessageType
    from src.core.whatsapp_plain_render import render_plain_template

    order_id = "2b1e6c3a-0000-4000-8000-000000000001"
    params = svc.template_params(
        "منى" if lang == "ar" else "Mona",
        "1042",
        "متجر القاهرة",
        svc.button_value("cairo-style", order_id),
        lang,
    )
    text = render_plain_template(MessageType(kind), lang, params).text
    assert "1042" in text and "متجر القاهرة" in text
    assert f"https://numueg.app/o/cairo-style/{order_id}" in text
    assert "{{" not in text


def test_a_missing_first_name_is_never_empty():
    assert svc.template_params(None, "1", "S", "s/1", "ar")["customer_name"] == "عميلنا"
    assert svc.template_params(" ", "1", "S", "s/1", "en")["customer_name"] == "there"
    assert svc.button_value(None, "abc") == "abc"


def test_settings_defaults():
    assert svc.merged_settings(None) == {
        "out_for_delivery_enabled": True,
        "failed_enabled": True,
        "daily_cap": 300,
        "show_items": True,
        "show_contact": True,
    }
    row = SimpleNamespace(**{**svc.DEFAULT_SETTINGS, "daily_cap": 5})
    assert svc.merged_settings(row)["daily_cap"] == 5
