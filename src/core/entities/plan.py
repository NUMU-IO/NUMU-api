"""Plan feature limits and pricing.

Defines what each tenant plan tier allows. All limit values of -1 mean unlimited.

Pricing model (Stream 4 of the NUMU plan):

* **No perpetual free tier.** ``free`` is retained in the dictionary for legacy
  data only — new signups go to ``trial`` and convert to ``starter`` or higher.
* **No per-order transaction fees.** NUMU collects 100% of revenue via
  predictable monthly subscriptions; merchants keep all of their order revenue.
  This eliminates the wallet/credit system and the CBE payment-aggregation
  license blocker.
* **30-day trial, then 30-day read-only grace, then hard delete.** The
  lifecycle state machine on the tenant model handles the transitions; this
  module just defines what each plan can *do*.

Prices are in EGP (Egyptian Pounds) and stored in piasters (1 EGP = 100 piasters)
to avoid floating-point math.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanFeatures:
    """Feature limits and pricing for a subscription plan."""

    # Resource limits
    max_products: int  # -1 = unlimited
    max_orders_per_month: int  # -1 = unlimited
    max_stores: int  # -1 = unlimited
    max_staff_members: int  # -1 = unlimited
    max_customers: int  # -1 = unlimited

    # Feature flags
    webhooks_enabled: bool
    custom_domain_enabled: bool
    api_access_enabled: bool
    analytics_enabled: bool
    discount_codes_enabled: bool

    # Pricing (piasters / month, 0 = free, -1 = custom contract)
    monthly_price_piasters: int
    annual_price_piasters: int  # discounted (~10 months for 12)

    # Display
    display_name: str


PLAN_LIMITS: dict[str, PlanFeatures] = {
    # ─── Internal sandbox plan, used by Try-a-Demo flow ───────────────────
    "demo": PlanFeatures(
        display_name="Demo",
        max_products=10,
        max_orders_per_month=50,
        max_stores=1,
        max_staff_members=1,
        max_customers=100,
        webhooks_enabled=False,
        custom_domain_enabled=False,
        api_access_enabled=False,
        analytics_enabled=True,  # show seeded analytics in demo dashboard
        discount_codes_enabled=False,
        monthly_price_piasters=0,
        annual_price_piasters=0,
    ),
    # ─── 30-day free trial of Starter features (default for new signups) ──
    "trial": PlanFeatures(
        display_name="Trial",
        max_products=100,
        max_orders_per_month=500,  # bounded so abuse is contained
        max_stores=1,
        max_staff_members=3,
        max_customers=5_000,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=False,
        analytics_enabled=True,
        discount_codes_enabled=True,
        monthly_price_piasters=0,
        annual_price_piasters=0,
    ),
    # ─── Paid plans ───────────────────────────────────────────────────────
    "starter": PlanFeatures(
        display_name="Starter",
        max_products=100,
        max_orders_per_month=-1,  # subscription, not metered
        max_stores=1,
        max_staff_members=3,
        max_customers=5_000,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=False,
        analytics_enabled=True,
        discount_codes_enabled=True,
        monthly_price_piasters=9_900,  # ج.م 99
        annual_price_piasters=99_000,  # ج.م 990 (10 months for the price of 12)
    ),
    "pro": PlanFeatures(
        display_name="Pro",
        max_products=-1,
        max_orders_per_month=-1,
        max_stores=3,
        max_staff_members=10,
        max_customers=-1,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=True,
        analytics_enabled=True,
        discount_codes_enabled=True,
        monthly_price_piasters=29_900,  # ج.م 299
        annual_price_piasters=299_000,  # ج.م 2,990
    ),
    "enterprise": PlanFeatures(
        display_name="Enterprise",
        max_products=-1,
        max_orders_per_month=-1,
        max_stores=-1,
        max_staff_members=-1,
        max_customers=-1,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=True,
        analytics_enabled=True,
        discount_codes_enabled=True,
        monthly_price_piasters=-1,  # custom contract
        annual_price_piasters=-1,
    ),
    # ─── Deprecated: legacy free plan ─────────────────────────────────────
    # Retained for backwards compatibility with existing tenant rows. New
    # signups must NOT land here. Treated identically to Trial for feature
    # gating but without an expiration sweep.
    "free": PlanFeatures(
        display_name="Free (legacy)",
        max_products=50,
        max_orders_per_month=100,
        max_stores=1,
        max_staff_members=1,
        max_customers=500,
        webhooks_enabled=False,
        custom_domain_enabled=False,
        api_access_enabled=False,
        analytics_enabled=False,
        discount_codes_enabled=False,
        monthly_price_piasters=0,
        annual_price_piasters=0,
    ),
}


def get_plan_features(plan: str) -> PlanFeatures:
    """Return feature limits for a plan name. Falls back to trial tier."""
    return PLAN_LIMITS.get(plan.lower(), PLAN_LIMITS["trial"])
