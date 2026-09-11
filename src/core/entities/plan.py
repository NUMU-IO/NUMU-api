"""Plan feature limits and pricing.

Defines what each tenant plan tier allows. All limit values of -1 mean unlimited.

Pricing model:

* **Subscriptions are the primary model.** New signups go to ``trial`` and
  convert to ``starter`` or higher; ``free`` is retained in the dictionary for
  legacy data only.
* **``payg`` (pay-as-you-go)** is the commission-funded alternative: no monthly
  fee; NUMU charges ``commission_bps`` of each PAID order, debited from a
  prepaid merchant wallet (``merchant_wallets``, WalletService). All other
  plans have ``commission_bps=0`` — merchants on subscriptions keep 100% of
  order revenue.
  ⚠ Regulatory note: per-order fees + a stored wallet balance were originally
  removed to sidestep the CBE payment-aggregation licensing question. The
  wallet holds prepayment of NUMU's own service fees (not pass-through
  consumer funds) and balances are refundable on request, but this framing
  must be cleared by counsel before payg GA.
* **37-day trial, then 30-day read-only grace, then hard delete.** The
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

    # Per-paid-order commission in basis points (100 bps = 1%), debited
    # from the tenant's prepaid wallet. Only payg is nonzero; a per-tenant
    # negotiated rate lives in merchant_wallets.commission_bps_override.
    commission_bps: int = 0


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
    # ─── 37-day free trial of Starter features (default for new signups) ──
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
        monthly_price_piasters=25_000,  # ج.م 250
        annual_price_piasters=250_000,  # ج.م 2,500 (10 months for the price of 12)
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
        monthly_price_piasters=49_900,  # ج.م 499
        annual_price_piasters=499_000,  # ج.م 4,990 (10 months for the price of 12)
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
    # ─── Pay-as-you-go: no subscription, wallet-funded commission ─────────
    # Starter-like features; orders are unmetered because the commission is
    # the meter. Requires a funded merchant wallet (checkout gate blocks
    # storefront orders below the negative allowance).
    "payg": PlanFeatures(
        display_name="Pay as you Grow",
        max_products=100,
        max_orders_per_month=-1,  # commission is the meter, not a cap
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
        commission_bps=300,  # 3% of each paid order
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
