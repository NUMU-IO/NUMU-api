"""Regression: the Meta business-scope OAuth router must be mounted.

`routes/oauth/meta.py` was implemented but never registered in
`routes/__init__.py`, so `/api/v1/oauth/meta/{start,callback}` returned 404 in
every environment — blocking the Facebook Login flow for the ads_management /
business_management / catalog_management / pages_show_list / instagram_basic
scopes under Meta App Review.
"""

from src.main import app


def _mounted_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_meta_oauth_routes_are_mounted():
    paths = _mounted_paths()
    assert "/api/v1/oauth/meta/start" in paths
    assert "/api/v1/oauth/meta/callback" in paths
