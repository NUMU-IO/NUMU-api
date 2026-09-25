"""COD rules: which orders need the phone OTP or a deposit, and the prepaid
incentive (COD Shield's checkout controls).

Stored in ``store.settings.cod_rules``:

* ``otp`` / ``deposit``: :class:`OrderConditions`. ``everyone`` keeps the
  existing behaviour (the OTP gate on every order, the deposit on every COD
  order that meets the deposit policy). Otherwise the rule applies only to a
  COD order matching at least one switched-on condition: a first-time
  customer, an order at or above a value, a high-risk order (the Trust
  Network score at or above the store's threshold), or a listed product or
  category in the cart.
* ``prepaid``: :class:`PrepaidIncentive`, a discount or free shipping for
  paying online instead of COD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

SETTINGS_KEY = "cod_rules"


class OrderConditions(BaseModel):
    everyone: bool = True
    first_time: bool = False
    min_order_cents: int | None = Field(default=None, ge=0)
    high_risk: bool = False
    product_ids: list[UUID] = Field(default_factory=list, max_length=200)
    category_ids: list[UUID] = Field(default_factory=list, max_length=100)


class PrepaidIncentive(BaseModel):
    enabled: bool = False
    kind: Literal["percent", "fixed", "free_shipping"] = "percent"
    percent: int = Field(default=5, ge=1, le=50)
    amount_cents: int = Field(default=0, ge=0)
    min_order_cents: int = Field(default=0, ge=0)


class CodRules(BaseModel):
    otp: OrderConditions = Field(default_factory=OrderConditions)
    deposit: OrderConditions = Field(default_factory=OrderConditions)
    prepaid: PrepaidIncentive = Field(default_factory=PrepaidIncentive)


def get_cod_rules(settings: dict | None) -> CodRules:
    """The store's rules; a missing or damaged block means the defaults
    (everything as it was before these rules existed)."""
    raw = (settings or {}).get(SETTINGS_KEY) or {}
    try:
        return CodRules.model_validate(raw)
    except ValidationError:
        return CodRules()


@dataclass(frozen=True)
class OrderFacts:
    is_cod: bool
    subtotal_cents: int
    first_time: bool
    high_risk: bool
    product_ids: frozenset[UUID] = field(default_factory=frozenset)
    category_ids: frozenset[UUID] = field(default_factory=frozenset)


def applies(conditions: OrderConditions, facts: OrderFacts) -> bool:
    """Whether a conditional rule covers this COD order."""
    if not facts.is_cod:
        return False
    if conditions.everyone:
        return True
    return (
        (conditions.first_time and facts.first_time)
        or (
            conditions.min_order_cents is not None
            and facts.subtotal_cents >= conditions.min_order_cents
        )
        or (conditions.high_risk and facts.high_risk)
        or bool(set(conditions.product_ids) & facts.product_ids)
        or bool(set(conditions.category_ids) & facts.category_ids)
    )


@dataclass(frozen=True)
class Incentive:
    discount_cents: int = 0
    free_shipping: bool = False

    def as_metadata(self) -> dict[str, Any]:
        return {
            "discount_cents": self.discount_cents,
            "free_shipping": self.free_shipping,
        }


def prepaid_incentive(
    rule: PrepaidIncentive, *, is_cod: bool, subtotal_cents: int, discount_cents: int
) -> Incentive:
    """What paying online earns this order. Never more than what is left of
    the subtotal after other discounts."""
    if not rule.enabled or is_cod or subtotal_cents < rule.min_order_cents:
        return Incentive()
    if rule.kind == "free_shipping":
        return Incentive(free_shipping=True)
    room = max(0, subtotal_cents - discount_cents)
    amount = room * rule.percent // 100 if rule.kind == "percent" else rule.amount_cents
    return Incentive(discount_cents=min(amount, room))
