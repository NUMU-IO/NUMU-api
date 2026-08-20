"""Target-role normalisation, shared by create and update.

## The trap this closes

A `PromotionTarget` with `role=None` is an **eligibility gate**: the
`PromotionEligibilityChecker` requires it to match before the promotion runs at
all, and then the rule applies to the whole cart. A target tagged
`role="buy_set"` is the opposite — a **line filter** the checker skips and the
discount calculator reads to decide which lines form a group.

For a percentage or fixed discount, "only runs when the cart contains a tee" is
a coherent offer and the gate is right. For a MULTIBUY it almost never is. A
merchant building "any 2 caps for 968" picks the Caps collection because that IS
the offer's subject, and if that pick lands as a gate:

  * the offer does not resolve until the cart ALREADY contains a cap, so the
    Build-a-Bundle page — whose entire audience is shoppers with an empty bag —
    renders nothing; and
  * once it does resolve, the group can be formed from any two items in the
    cart, because the gate is not a line filter. Two towels for the cap price.

Both failures are silent and neither is visible in the merchant UI, which has no
concept of "role" to show. So the mapping is inferred here instead, once, on the
write path.

## Why this is safe to infer

Only when ALL of these hold:

  * the rule is a MULTIBUY (BUNDLE is excluded on purpose — see below),
  * the promotion carries no explicit `buy_set` already, so a merchant or an
    API client that did tag its targets is never second-guessed, and
  * the target is an INCLUSION rule of a catalog kind. An exclusion
    ("...but not clearance") is a genuine gate and stays one, and audience /
    geo / customer-tag rows are not line filters at all.

BUNDLE is left alone because there is nothing to infer: its scopes are
per-leg (`role="leg:0"`, `"leg:1"`, …) and an untagged catalog target carries no
information about WHICH leg it belongs to. Guessing would silently attach the
tees to leg 0 and price a two-tee cart as "1 tee + 1 cap".
"""

from src.core.entities.promotion_target import PromotionTarget
from src.core.enums.promotion_enums import TargetKind
from src.core.value_objects.discount_rule import DiscountRule, DiscountRuleKind

_CATALOG_KINDS = {TargetKind.PRODUCT, TargetKind.CATEGORY}


def normalize_target_roles(
    rule: DiscountRule | None, targets: list[PromotionTarget]
) -> list[PromotionTarget]:
    """Return `targets` with multibuy scope promoted to `buy_set`.

    Pure: builds new entities rather than mutating the inputs, so a caller can
    log or diff the before/after. Returns the list unchanged (same objects) when
    there is nothing to promote.
    """
    if rule is None or rule.kind != DiscountRuleKind.MULTIBUY:
        return targets
    if any(t.role == "buy_set" for t in targets):
        return targets

    promoted = False
    out: list[PromotionTarget] = []
    for t in targets:
        if t.role is None and t.inclusion and t.target_kind in _CATALOG_KINDS:
            out.append(t.model_copy(update={"role": "buy_set"}))
            promoted = True
        else:
            out.append(t)
    return out if promoted else targets
