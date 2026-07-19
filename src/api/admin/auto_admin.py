"""Django-admin-style auto-registration for the long tail of models.

SQLAdmin requires one ``ModelView`` per model. Hand-writing 50+ of them is the
opposite of Django's ``admin.site.register(Model)`` ergonomics, so this module
generates a view (with safe defaults) for every model exported from
``src.infrastructure.database.models.__all__`` that does **not** already have a
curated view in :mod:`src.api.admin.views`.

Design choices (all deliberate, see the build note in the PR):

* **Scalar columns only.** Generated views never list/expand relationships.
  ``column_list = "__all__"`` would pull one-to-many collections into the list
  and detail pages via ``selectinload`` (SQLAdmin eager-loads relations to stay
  async-safe), so rendering a single Store row would load its entire ``products``
  collection just to fill a cell. Django's ``list_display`` defaults to fields,
  not reverse relations, and we match that.
* **Sensitive tables are read-only.** Payments, trust signals, event/audit logs
  and PII get ``can_create = can_edit = can_delete = False`` — visible and
  exportable, but not editable by hand. See :func:`_is_readonly`.
* **Sidebar grouping.** Each view is assigned a ``category`` so the admin sidebar
  reads like Django's per-app sections.
"""

from __future__ import annotations

import re
import types
from typing import Any

from sqladmin import Admin, ModelView
from sqlalchemy import inspect as sa_inspect

from src.infrastructure.database import models as models_module

# ── Sidebar groups (Django "app" sections) ───────────────────────────────────
_CATEGORY_ICONS = {
    "Identity": "fa-solid fa-id-badge",
    "Catalog": "fa-solid fa-layer-group",
    "Commerce": "fa-solid fa-cart-shopping",
    "Promotions": "fa-solid fa-tags",
    "Payments": "fa-solid fa-credit-card",
    "Messaging": "fa-solid fa-comments",
    "Integrations": "fa-solid fa-plug",
    "Trust & Risk": "fa-solid fa-shield-halved",
    "Automation": "fa-solid fa-robot",
    "Events & Logs": "fa-solid fa-bolt",
    "System": "fa-solid fa-gear",
    "Other": "fa-solid fa-table",
}


def _categorize(name: str) -> str:
    """Map a model class name to a sidebar category (first match wins)."""
    n = name.lower()
    if "shopify" in n or "catalogmapping" in n:
        return "Integrations"
    if any(k in n for k in ("payment", "invoice", "refund", "instapay", "wallet")):
        return "Payments"
    if any(k in n for k in ("network", "reputation", "risk")):
        return "Trust & Risk"
    if "promotion" in n:
        return "Promotions"
    if "automation" in n:
        return "Automation"
    if any(k in n for k in ("waitlist", "feedback", "onboarding", "config")):
        return "System"
    if "theme" in n:
        return "Catalog"
    if any(k in n for k in ("channel", "message", "thread", "whatsapp", "social")):
        return "Messaging"
    if (
        any(k in n for k in ("webhook", "capievent", "metaevent", "pageview", "audit"))
        or n.endswith("logmodel")
        or n.endswith("eventmodel")
    ):
        return "Events & Logs"
    if any(k in n for k in ("product", "category", "store")):
        return "Catalog"
    if any(k in n for k in ("order", "customer", "coupon", "shipment")):
        return "Commerce"
    if any(
        k in n for k in ("tenant", "user", "role", "membership", "permission", "staff")
    ):
        return "Identity"
    return "Other"


# Whole categories that are never editable from the admin.
_READONLY_CATEGORIES = {"Payments", "Trust & Risk", "Events & Logs"}
# Individually read-only models that fall outside those categories
# (PII, message content, high-volume analytics rows).
_READONLY_EXTRA = {
    "TenantMembershipModel",  # access-control wiring — view only
    "RoleModel",  # RBAC roles — editing raw CRUD risks escalation
    "CustomerAddressModel",  # PII (addresses)
    "ChannelMessageModel",  # customer message content
    "MessageLogModel",  # message delivery logs
    "AutomationLogModel",  # automation execution logs
    "PromotionEventModel",  # analytics
    "PromotionEventDailyModel",  # analytics rollups
    "PromotionDismissalModel",  # analytics
    "PromotionDisplayModel",  # analytics
}
# Models that look like logs but are actually editable config.
_EDITABLE_OVERRIDE = {"WebhookSubscriptionModel"}


def _is_readonly(name: str, category: str) -> bool:
    if name in _EDITABLE_OVERRIDE:
        return False
    return category in _READONLY_CATEGORIES or name in _READONLY_EXTRA


_TIMESTAMPS = ("created_at", "updated_at")


def _scalar_columns(model: Any) -> list[str]:
    """Ordered scalar column attr names (no relationships).

    ``id`` first, timestamps last, declared order in between — so the list/detail
    pages read naturally and never touch relationship loaders.
    """
    mapper = sa_inspect(model)
    names = [attr.key for attr in mapper.column_attrs]
    head = [n for n in names if n == "id"]
    tail = [n for n in names if n in _TIMESTAMPS]
    middle = [n for n in names if n != "id" and n not in _TIMESTAMPS]
    return head + middle + tail


# Proper nouns the camel-case splitter can't recover on its own.
_NAME_OVERRIDES = {
    "WhatsAppTemplateModel": "WhatsApp Template",
    "CapiEventModel": "CAPI Event",
    "InstapayIntentModel": "InstaPay Intent",
}


def _nice_name(model_name: str) -> str:
    if model_name in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[model_name]
    base = model_name[:-5] if model_name.endswith("Model") else model_name
    # Split only at a lower→upper boundary so leading acronyms survive.
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)


def _pluralize(name: str) -> str:
    if name.endswith("y"):
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    return name + "s"


def build_auto_view(model: Any) -> type[ModelView]:
    """Generate a ``ModelView`` subclass for ``model`` with safe defaults.

    Uses :func:`types.new_class` so the SQLAdmin metaclass receives ``model=...``
    exactly as it would from ``class XAdmin(ModelView, model=X)``.
    """
    model_name = model.__name__
    category = _categorize(model_name)
    cols = _scalar_columns(model)
    nice = _nice_name(model_name)

    attrs: dict[str, Any] = {
        "column_list": cols,
        "column_details_list": cols,
        "column_export_list": cols,
        "can_export": True,
        "category": category,
        "icon": _CATEGORY_ICONS.get(category, _CATEGORY_ICONS["Other"]),
        "name": nice,
        "name_plural": _pluralize(nice),
        "page_size": 25,
    }
    if _is_readonly(model_name, category):
        attrs.update(can_create=False, can_edit=False, can_delete=False)
    else:
        # Raw scalar fields only: keeps creation/editing free of mass FK-option
        # loading (a relationship field would query every possible parent row).
        attrs["form_columns"] = [c for c in cols if c not in ("id", *_TIMESTAMPS)]

    return types.new_class(
        f"{model_name}AutoAdmin",
        (ModelView,),
        {"model": model},
        lambda ns: ns.update(attrs),
    )


def _iter_exported_models() -> list[Any]:
    """All mapped model classes exported via ``models.__all__`` (deduped).

    Mixins (``TimestampMixin`` etc.) are skipped — they are not mapped, so they
    have no ``__mapper__``.
    """
    seen: set[Any] = set()
    out: list[Any] = []
    for attr_name in getattr(models_module, "__all__", []):
        obj = getattr(models_module, attr_name, None)
        if isinstance(obj, type) and hasattr(obj, "__mapper__") and obj not in seen:
            seen.add(obj)
            out.append(obj)
    return out


def register_auto_views(admin: Admin, *, exclude: set[Any]) -> list[str]:
    """Register a generated view for every exported model not in ``exclude``.

    Returns the registered model names (for startup logging). Curated views in
    ``exclude`` keep ownership of their models.
    """
    registered: list[str] = []
    for model in _iter_exported_models():
        if model in exclude:
            continue
        admin.add_view(build_auto_view(model))
        registered.append(model.__name__)
    return registered
