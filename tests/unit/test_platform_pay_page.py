"""The platform card page ships with its own locked-down CSP.

Merchants type cards on this page, so it may talk only to Kashier's card
endpoint, run only its own script, and be framed only by NUMU's apps.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.security_headers import SecurityHeadersMiddleware
from src.api.v1.routes.platform_pay import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_card_page_is_framable_only_by_numu_and_talks_only_to_kashier():
    res = _client().get("/api/v1/platform-pay/card")
    csp = res.headers["content-security-policy"]
    assert res.status_code == 200
    assert "frame-ancestors https://*.numueg.app" in csp
    assert "script-src 'self';" in csp
    assert "connect-src https://fep.kashier.io https://test-fep.kashier.io;" in csp
    assert "x-frame-options" not in res.headers
    assert res.headers["cache-control"] == "no-store"
    assert 'src="card.js?v=2"' in res.text

    script = _client().get("/api/v1/platform-pay/card.js?v=2")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "public, max-age=300"


def test_other_api_responses_keep_the_default_policy():
    app = FastAPI()

    @app.get("/api/v1/other")
    async def other():
        return {}

    app.add_middleware(SecurityHeadersMiddleware)
    res = TestClient(app).get("/api/v1/other")
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
