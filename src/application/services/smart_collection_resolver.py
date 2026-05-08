"""Smart-collection rule resolver (Phase 4.4).

Categories can carry a `rules` blob in `extra_data` that drives
membership automatically — Shopify's "Smart Collection" pattern.

Rule shape (stored on `category.extra_data.smart_rules`):

    {
      "match_all": true,                      // all rules vs any
      "rules": [
        { "field": "tags",      "op": "contains",     "value": "sale" },
        { "field": "price",     "op": "gt",           "value": 5000   },
        { "field": "category_path","op": "equals",    "value": "shoes" },
        { "field": "name",      "op": "contains",     "value": "hoodie" }
      ]
    }

Supported (field, op) combinations:
    tags        — contains, not_contains
    price       — gt, lt, gte, lte, equals
    name        — contains, not_contains, equals
    sku         — equals, contains
    created_at  — gt, lt   (ISO 8601)
    category_id — equals   (one product belongs to one category — this is
                            "products in OTHER category X with these rules")

Why `match_all` vs `match_any` (not "any/all"):
    Shopify uses "all conditions must match" / "any condition matches".
    We pick the boolean shape so JSON-schema validation stays terse:
    one boolean instead of an enum union.

Why a separate resolver vs an SQL query:
    Some operators (like compound tag matching) are hard to express
    in pure SQL without per-store schema. The resolver runs over a
    bounded product list — the Celery sweep pulls active products
    page-by-page, applies the rules, and writes the membership.
    For v1 the rule-evaluation cost is dominated by the I/O of
    fetching products, not the predicate cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Rule:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class SmartCollectionRules:
    rules: list[Rule]
    match_all: bool = True

    @property
    def is_empty(self) -> bool:
        return not self.rules

    @classmethod
    def from_dict(cls, raw: Any) -> SmartCollectionRules | None:
        """Parse the raw `extra_data.smart_rules` JSON. Returns None
        when the blob is missing or malformed — caller treats that as
        "this is a manual collection, leave membership alone".
        """
        if not isinstance(raw, dict):
            return None
        rules_raw = raw.get("rules")
        if not isinstance(rules_raw, list) or not rules_raw:
            return None
        rules: list[Rule] = []
        for entry in rules_raw:
            if not isinstance(entry, dict):
                continue
            field = entry.get("field")
            op = entry.get("op")
            value = entry.get("value")
            if not isinstance(field, str) or not isinstance(op, str):
                continue
            rules.append(Rule(field=field, op=op, value=value))
        if not rules:
            return None
        match_all = bool(raw.get("match_all", True))
        return cls(rules=rules, match_all=match_all)


def _coerce_number(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_datetime(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _eval_rule(rule: Rule, product: Any) -> bool:
    """Evaluate a single rule against a Product entity.

    Unknown (field, op) combinations return False — treating an
    unrecognized rule as "doesn't match" is safer than as "matches"
    because it prevents accidentally including everything when a
    merchant typos a rule field.
    """
    field = rule.field
    op = rule.op
    value = rule.value

    if field == "tags":
        tags = getattr(product, "tags", None) or []
        if not isinstance(tags, list):
            return False
        if op == "contains":
            return value in tags
        if op == "not_contains":
            return value not in tags
        return False

    if field == "name":
        name = getattr(product, "name", "") or ""
        v = str(value or "").lower()
        if not v:
            return False
        if op == "contains":
            return v in name.lower()
        if op == "not_contains":
            return v not in name.lower()
        if op == "equals":
            return name.lower() == v
        return False

    if field == "sku":
        sku = (getattr(product, "sku", None) or "").lower()
        v = str(value or "").lower()
        if op == "equals":
            return sku == v
        if op == "contains":
            return v in sku
        return False

    if field == "price":
        # Product price is a Money value object — read .cents for int
        # comparison. Rules store the price in major units (e.g. 50.00
        # for EGP 50.00); convert to cents on the way in so we don't
        # multiply mid-eval per product.
        price_obj = getattr(product, "price", None)
        if price_obj is None:
            return False
        price_cents = getattr(price_obj, "cents", None)
        if price_cents is None:
            return False
        target = _coerce_number(value)
        if target is None:
            return False
        target_cents = int(target * 100)
        if op == "gt":
            return price_cents > target_cents
        if op == "gte":
            return price_cents >= target_cents
        if op == "lt":
            return price_cents < target_cents
        if op == "lte":
            return price_cents <= target_cents
        if op == "equals":
            return price_cents == target_cents
        return False

    if field == "category_id":
        cat_id = getattr(product, "category_id", None)
        if op == "equals":
            return str(cat_id) == str(value)
        return False

    if field == "created_at":
        created = getattr(product, "created_at", None)
        target = _coerce_datetime(value)
        if created is None or target is None:
            return False
        if op == "gt":
            return created > target
        if op == "lt":
            return created < target
        return False

    return False


def matches(rules: SmartCollectionRules, product: Any) -> bool:
    """Apply the full rule set to one product.

    `match_all` short-circuits on the first miss; `match_any`
    short-circuits on the first hit. Empty rule sets return False —
    a smart collection with no rules has no members (rather than
    everything), preventing a half-configured rule from accidentally
    including the whole catalog.
    """
    if rules.is_empty:
        return False
    if rules.match_all:
        for r in rules.rules:
            if not _eval_rule(r, product):
                return False
        return True
    # match_any
    for r in rules.rules:
        if _eval_rule(r, product):
            return True
    return False
