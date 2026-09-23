"""Every attribution field cap must fit the column the value is stored in.

Raising a schema cap without widening its column only moves the failure
from a 422 to a database error on insert.
"""

from annotated_types import MaxLen

from src.core.entities.attribution import AttributionTouch
from src.infrastructure.database.models.tenant.customer_touch import (
    CustomerTouchModel,
)


def test_touch_caps_fit_customer_touch_columns():
    columns = CustomerTouchModel.__table__.columns
    checked = 0
    for name, field in AttributionTouch.model_fields.items():
        cap = next(
            (m.max_length for m in field.metadata if isinstance(m, MaxLen)), None
        )
        if cap is None or name not in columns:
            continue
        width = getattr(columns[name].type, "length", None)
        assert width is None or cap <= width, f"{name}: cap {cap} > column {width}"
        checked += 1
    assert checked >= 8
