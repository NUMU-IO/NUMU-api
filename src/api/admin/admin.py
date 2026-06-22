"""SQLAdmin setup and configuration."""

from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy import func, select, text
from starlette.requests import Request
from starlette.responses import Response

from src.api.admin.auth import AdminAuth
from src.api.admin.auto_admin import register_auto_views
from src.api.admin.views import (
    CategoryAdmin,
    CustomerAdmin,
    OrderAdmin,
    ProductAdmin,
    StoreAdmin,
    TenantAdmin,
    UserAdmin,
)
from src.config import settings
from src.config.logging_config import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal, engine

logger = get_logger(__name__)

_TEMPLATES_DIR = str(Path(__file__).parent / "templates")

# Headline entities rendered as metric cards on the dashboard:
# (label, model identity slug, icon).
_DASHBOARD_CARDS = [
    ("Tenants", "tenant-model", "fa-solid fa-building"),
    ("Stores", "store-model", "fa-solid fa-store"),
    ("Products", "product-model", "fa-solid fa-box"),
    ("Orders", "order-model", "fa-solid fa-shopping-cart"),
    ("Customers", "customer-model", "fa-solid fa-users"),
    ("Users", "user-model", "fa-solid fa-user"),
]


class DashboardAdmin(Admin):
    """Admin whose index renders a real dashboard instead of a blank page.

    SQLAdmin's default ``index.html`` is empty (``{% block content %}{% endblock
    %}``); the base ``index`` method exists explicitly to be overridden. We render
    our own ``index.html`` (found first via ``templates_dir``) with live counts.
    """

    async def index(self, request: Request) -> Response:
        from src.infrastructure.database.models import (
            CustomerModel,
            OrderModel,
            ProductModel,
            StoreModel,
            TenantModel,
            UserModel,
        )

        models_by_slug = {
            "tenant-model": TenantModel,
            "store-model": StoreModel,
            "product-model": ProductModel,
            "order-model": OrderModel,
            "customer-model": CustomerModel,
            "user-model": UserModel,
        }

        metrics: list[dict] = []
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SET search_path TO public"))
                for label, slug, icon in _DASHBOARD_CARDS:
                    try:
                        count = (
                            await session.execute(
                                select(func.count()).select_from(models_by_slug[slug])
                            )
                        ).scalar_one()
                    except Exception:
                        count = None  # one bad table shouldn't blank the dashboard
                    metrics.append(
                        {"label": label, "slug": slug, "icon": icon, "count": count}
                    )
        except Exception:
            logger.warning("admin_dashboard_metrics_failed", exc_info=True)
            metrics = [
                {"label": label, "slug": slug, "icon": icon, "count": None}
                for label, slug, icon in _DASHBOARD_CARDS
            ]

        return await self.templates.TemplateResponse(
            request, "index.html", {"metrics": metrics}
        )


# Hand-tuned views. These own their models; the auto-registrar skips them.
CURATED_VIEWS = [
    TenantAdmin,
    UserAdmin,
    StoreAdmin,
    CategoryAdmin,
    ProductAdmin,
    CustomerAdmin,
    OrderAdmin,
]


def setup_admin(app: FastAPI) -> Admin:
    """Setup SQLAdmin with the FastAPI application.

    Registers the curated views, then auto-registers a default view for every
    other exported model (Django ``admin.site.register`` parity). See
    :mod:`src.api.admin.auto_admin`.

    Args:
        app: The FastAPI application instance.

    Returns:
        The configured Admin instance.
    """
    # Create admin instance with authentication + dashboard index
    admin = DashboardAdmin(
        app=app,
        engine=engine,
        authentication_backend=AdminAuth(secret_key=settings.session_secret_key),
        title="NUMU Admin",
        base_url="/admin",
        templates_dir=_TEMPLATES_DIR,
    )

    # Curated views first so they win ownership of their models.
    for view in CURATED_VIEWS:
        admin.add_view(view)

    # Auto-register the long tail (sensitive tables come out read-only).
    auto = register_auto_views(admin, exclude={view.model for view in CURATED_VIEWS})
    logger.info(
        "admin_views_registered",
        curated=len(CURATED_VIEWS),
        auto=len(auto),
        auto_models=auto,
    )

    return admin
