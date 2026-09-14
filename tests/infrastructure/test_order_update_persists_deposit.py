"""OrderRepository.update() must persist every field mutated after creation.

`update()` copies entity fields onto the model by hand. Anything missing from
that list is accepted by the entity, flushed without error, and silently never
written — the row keeps its old value with no failure anywhere.

That is how a COD deposit order ended up in PENDING_DEPOSIT with a NULL
`deposit_gateway`: checkout creates the order, then stamps the deposit columns
and calls update(). `status` was on the list, the six `deposit_*` columns were
not. The customer then uploaded their transfer receipt and got "This order was
not placed with a transfer-based payment method", because the proof check reads
`deposit_gateway` to recognise a manual deposit.

Source-level on purpose: the failure is an assignment that isn't there, so the
check is that it is.
"""

import inspect
import re

from src.core.entities.order import Order
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.repositories.order_repository import OrderRepository

#: Columns written after the order row exists. Every one of these has a code
#: path that sets it on a loaded entity and calls update().
MUTATED_AFTER_CREATE = {
    # Deposit-to-confirm COD — set by checkout when diverting into
    # PENDING_DEPOSIT, and by the gateway webhook when the deposit lands.
    "deposit_required_cents",
    "deposit_amount_cents",
    "deposit_paid_at",
    "deposit_expires_at",
    "deposit_gateway",
    "deposit_payment_id",
    # WhatsApp tap-to-confirm.
    "customer_confirmation_status",
    "customer_confirmation_requested_at",
    "customer_confirmed_at",
    # Lifecycle.
    "status",
    "payment_status",
    "fulfillment_status",
    "payment_method",
    "payment_id",
    "paid_at",
    "cancelled_at",
    "fulfilled_at",
    # Partial acceptance at the door.
    "collected_total",
}


def _assigned_in_update() -> set[str]:
    source = inspect.getsource(OrderRepository.update)
    return set(re.findall(r"model\.(\w+)\s*=", source))


def test_update_persists_every_post_create_field() -> None:
    missing = MUTATED_AFTER_CREATE - _assigned_in_update()
    assert not missing, (
        f"OrderRepository.update() never writes {sorted(missing)}. "
        "Setting them on the entity will appear to succeed and be lost."
    )


def test_deposit_fields_exist_on_both_sides() -> None:
    """A typo'd assignment would pass the test above while writing nothing."""
    columns = {c.name for c in OrderModel.__table__.columns}
    for field in MUTATED_AFTER_CREATE:
        assert field in columns, f"{field} is not a column on OrderModel"
        assert field in Order.model_fields, f"{field} is not a field on Order"
