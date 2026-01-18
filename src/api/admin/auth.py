"""Admin authentication backend for SQLAdmin."""

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
from sqlalchemy import select, text

from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models import UserModel
from src.infrastructure.external_services.password_service import password_service
from src.core.entities.user import UserRole


class AdminAuth(AuthenticationBackend):
    """Authentication backend for admin panel.
    
    Only users with SUPER_ADMIN role can access the admin panel.
    """

    async def login(self, request: Request) -> bool:
        """Validate admin login credentials."""
        form = await request.form()
        email = form.get("username")  # SQLAdmin uses 'username' field
        password = form.get("password")

        if not email or not password:
            return False

        try:
            async with AsyncSessionLocal() as session:
                # Explicitly set public schema for user lookup
                await session.execute(text("SET search_path TO public"))
                
                result = await session.execute(
                    select(UserModel).where(UserModel.email == email)
                )
                user = result.scalar_one_or_none()

                if not user:
                    return False

                # Verify password
                if not password_service.verify_password(str(password), user.hashed_password):
                    return False

                # Check if user is a super admin
                if user.role != UserRole.SUPER_ADMIN:
                    return False

                # Store user info in session
                request.session.update({
                    "admin_user_id": str(user.id),
                    "admin_email": user.email,
                })
                return True
        except Exception as e:
            print(f"Admin login error: {e}")
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
