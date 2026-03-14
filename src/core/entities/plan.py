"""Plan feature limits.

Defines what each tenant plan tier allows. All limit values of -1 mean unlimited.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanFeatures:
    """Feature limits for a subscription plan."""

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

    # Display
    display_name: str


PLAN_LIMITS: dict[str, PlanFeatures] = {
    "free": PlanFeatures(
        display_name="Free",
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
    ),
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
        analytics_enabled=False,
        discount_codes_enabled=False,
    ),
    "starter": PlanFeatures(
        display_name="Starter",
        max_products=500,
        max_orders_per_month=1_000,
        max_stores=1,
        max_staff_members=3,
        max_customers=5_000,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=False,
        analytics_enabled=True,
        discount_codes_enabled=True,
    ),
    "pro": PlanFeatures(
        display_name="Pro",
        max_products=5_000,
        max_orders_per_month=10_000,
        max_stores=3,
        max_staff_members=10,
        max_customers=-1,
        webhooks_enabled=True,
        custom_domain_enabled=True,
        api_access_enabled=True,
        analytics_enabled=True,
        discount_codes_enabled=True,
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
    ),
}


def get_plan_features(plan: str) -> PlanFeatures:
    """Return feature limits for a plan name. Falls back to free tier."""
    return PLAN_LIMITS.get(plan.lower(), PLAN_LIMITS["free"])
