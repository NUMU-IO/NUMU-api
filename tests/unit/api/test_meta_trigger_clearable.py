"""An explicit ``purchase_trigger: null`` must be distinguishable from an omitted field.

The Meta tracking PUT keeps a stored trigger when the request omits it. It used
to decide that with ``is not None``, so an explicit null was also "keep" and no
client could clear a trigger. The route now keys on ``model_fields_set``; these
tests pin the schema contract that makes that work.
"""

from src.api.v1.schemas.tenant.tracking import SaveMetaTrackingRequest

_BASE = {"pixel_id": "123456789012345", "pixel_enabled": True, "capi_enabled": True}


def _resolve(req: SaveMetaTrackingRequest, field: str, stored: str | None):
    # The exact expression the route uses for both triggers.
    return getattr(req, field) if field in req.model_fields_set else stored


def test_omitted_trigger_keeps_the_stored_value():
    req = SaveMetaTrackingRequest(**_BASE)
    assert "purchase_trigger" not in req.model_fields_set
    assert _resolve(req, "purchase_trigger", "confirmed") == "confirmed"
    assert _resolve(req, "lead_trigger", "confirmed") == "confirmed"


def test_explicit_null_clears_the_stored_value():
    req = SaveMetaTrackingRequest(**_BASE, purchase_trigger=None, lead_trigger=None)
    assert "purchase_trigger" in req.model_fields_set
    assert _resolve(req, "purchase_trigger", "confirmed") is None
    assert _resolve(req, "lead_trigger", "confirmed") is None


def test_explicit_value_replaces_the_stored_value():
    req = SaveMetaTrackingRequest(**_BASE, purchase_trigger="shipped")
    assert _resolve(req, "purchase_trigger", "confirmed") == "shipped"
    assert _resolve(req, "lead_trigger", "confirmed") == "confirmed"


def test_json_null_counts_as_explicit():
    req = SaveMetaTrackingRequest.model_validate({**_BASE, "purchase_trigger": None})
    assert "purchase_trigger" in req.model_fields_set
