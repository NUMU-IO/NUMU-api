"""Admin authentication backend for SQLAdmin."""

import logging

from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select, text
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.core.entities.user import UserRole
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models import UserModel
from src.infrastructure.external_services.password_service import password_service

logger = logging.getLogger(__name__)


class AdminAuth(AuthenticationBackend):
    """Authentication backend for admin panel.

    Only users with SUPER_ADMIN role can access the admin panel.
    """

    async def login(self, request: Request) -> bool:
        """Validate admin login credentials."""
        form = await request.form()
        email = form.get("username")  # SQLAdmin uses 'username' field
        password = form.get("password")

        # Strip whitespace from email
        if email:
            email = str(email).strip().lower()

        logger.info(f"Admin login attempt for: {email}")

        if not email or not password:
            logger.warning("Missing email or password")
            return False

        try:
            async with AsyncSessionLocal() as session:
                # Explicitly set public schema for user lookup
                await session.execute(text("SET search_path TO public"))

                result = await session.execute(
                    select(UserModel).where(UserModel.email == email.lower())
                )
                user = result.scalar_one_or_none()

                if not user:
                    logger.warning(f"User not found: {email}")
                    return False

                # Verify password
                if not password_service.verify_password(
                    str(password), user.hashed_password
                ):
                    logger.warning(f"Invalid password for: {email}")
                    return False

                # Check if user is a super admin
                if user.role != UserRole.SUPER_ADMIN:
                    logger.warning(
                        f"User {email} is not a SUPER_ADMIN, role: {user.role}"
                    )
                    return False

                # Store user info in session
                request.session.update({
                    "admin_user_id": str(user.id),
                    "admin_email": user.email,
                })
                logger.info(f"Admin login successful for: {email}")
                return True
        except Exception as e:
            logger.exception("Admin login error: %s", e)
            return False

    async def logout(self, request: Request) -> bool:
        """Handle admin logout."""
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> RedirectResponse | bool:
        """Check if the request is authenticated."""
        admin_user_id = request.session.get("admin_user_id")

        if not admin_user_id:
            return RedirectResponse(request.url_for("admin:login"), status_code=302)

        return True
