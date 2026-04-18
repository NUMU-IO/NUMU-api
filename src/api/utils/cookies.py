"""Cookie utilities for httpOnly token auth.

Cookie namespaces:
    - `access_token` / `refresh_token`          — merchants + customers
      (shared with `cookie_domain=.numueg.app` so the merchant hub works
      across subdomains)
    - `admin_access_token` / `admin_refresh_token` — platform admins
      (kept on a separate cookie name so impersonation can mint merchant
      cookies on the admin user's browser without overwriting the admin
      session, which caused the admin panel to show the merchant's email
      after clicking "Log in as merchant")
    - `customer_access_token` / `customer_refresh_token` — storefront
      customers (cookie name predates the split)
"""

from fastapi import Response

from src.config import settings


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set httpOnly auth cookies on the response."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/api/v1/auth/refresh",
        max_age=settings.refresh_token_expire_days * 86400,
    )


def clear_auth_cookies(response: Response) -> None:
    """Delete auth cookies."""
    response.delete_cookie(key="access_token", path="/", domain=settings.COOKIE_DOMAIN)
    response.delete_cookie(
        key="refresh_token",
        path="/api/v1/auth/refresh",
        domain=settings.COOKIE_DOMAIN,
    )


def set_customer_auth_cookies(
    response: Response, access_token: str, refresh_token: str
) -> None:
    """Set httpOnly auth cookies for storefront customers."""
    response.set_cookie(
        key="customer_access_token",
        value=access_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="customer_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/api/v1/storefront/",
        max_age=settings.refresh_token_expire_days * 86400,
    )


def clear_customer_auth_cookies(response: Response) -> None:
    """Delete customer auth cookies."""
    response.delete_cookie(
        key="customer_access_token", path="/", domain=settings.COOKIE_DOMAIN
    )
    response.delete_cookie(
        key="customer_refresh_token",
        path="/api/v1/storefront/",
        domain=settings.COOKIE_DOMAIN,
    )


def set_admin_auth_cookies(
    response: Response, access_token: str, refresh_token: str
) -> None:
    """Set httpOnly auth cookies for platform admins.

    Uses distinct cookie names so admin sessions survive operations that
    mint a regular `access_token` on the same parent domain (notably the
    "Log in as merchant" impersonation flow).
    """
    response.set_cookie(
        key="admin_access_token",
        value=access_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        key="admin_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite=settings.SAMESITE_COOKIES,
        domain=settings.COOKIE_DOMAIN,
        path="/api/v1/admin/auth/refresh",
        max_age=settings.refresh_token_expire_days * 86400,
    )


def clear_admin_auth_cookies(response: Response) -> None:
    """Delete admin auth cookies."""
    response.delete_cookie(
        key="admin_access_token", path="/", domain=settings.COOKIE_DOMAIN
    )
    response.delete_cookie(
        key="admin_refresh_token",
        path="/api/v1/admin/auth/refresh",
        domain=settings.COOKIE_DOMAIN,
    )
