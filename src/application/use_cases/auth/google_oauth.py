"""Google OAuth login/register use case.

Verifies a Google ID token, creates or finds the user, and returns
an AuthResponseDTO with JWT tokens — same shape as normal login.
"""

from datetime import UTC, datetime, timedelta

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from src.application.dto.auth import AuthResponseDTO, TokenDTO
from src.application.dto.user import UserDTO
from src.config import settings
from src.core.entities.user import User, UserRole, UserStatus
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.services.token_service import ITokenService
from src.core.logging import get_logger
from src.core.value_objects.email import Email
from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber

logger = get_logger(__name__)


class GoogleOAuthUseCase:
    """Authenticate or register a user via Google ID token."""

    def __init__(
        self,
        user_repository: IUserRepository,
        token_service: ITokenService,
    ) -> None:
        self.user_repository = user_repository
        self.token_service = token_service

    async def execute(
        self, id_token_str: str, phone: str | None = None
    ) -> AuthResponseDTO:
        """Verify Google ID token and return auth response.

        If the user doesn't exist, creates a new account (auto-verified).
        If the user exists (by google_id or email), logs them in.

        *phone* is whatever the client managed to collect before handing
        off to Google. Google itself never returns a number, so unlike the
        other two signup doors this one cannot demand it — an account
        created here may legitimately have ``phone`` unset, and
        ``users.phone IS NULL`` is the flag the hub uses to collect it
        later. A number is only ever written when the user has none: a
        merchant who set their number in the hub does not get it silently
        replaced by whatever a landing form happened to be holding.
        """
        log = logger.bind(provider="google")

        # 1. Verify token with Google
        client_id = settings.google_oauth_client_id
        if not client_id:
            raise ValueError("Google OAuth is not configured (missing client ID)")

        try:
            idinfo = google_id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                client_id,
            )
        except Exception as e:
            log.warning("google_oauth_token_invalid", error=str(e))
            raise ValueError(f"Invalid Google token: {e}") from e

        # 2. Extract user info from verified token
        google_sub = idinfo["sub"]
        email_str = idinfo.get("email", "")
        email_verified = idinfo.get("email_verified", False)
        first_name = idinfo.get("given_name", "")
        last_name = idinfo.get("family_name", "")
        avatar_url = idinfo.get("picture")

        if not email_str or not email_verified:
            raise ValueError("Google account email is not verified")

        log = log.bind(email=email_str, google_sub=google_sub)

        # A malformed number is not worth failing a sign-in over: this is
        # a best-effort capture, and the hub asks again when it is unset.
        parsed_phone: PhoneNumber | None = None
        if phone:
            try:
                parsed_phone = PhoneNumber.parse(phone)
            except InvalidPhoneError:
                log.warning("google_oauth_phone_unparseable")

        # 3. Find existing user by google_id
        user = await self.user_repository.get_by_google_id(google_sub)

        if not user:
            # 4. Try finding by email (existing user linking Google)
            user = await self.user_repository.get_by_email_str(email_str)

            if user:
                # Link Google account to existing user
                user.google_id = google_sub
                user.auth_provider = user.auth_provider or "google"
                if avatar_url and not user.avatar_url:
                    user.avatar_url = avatar_url
                if parsed_phone and not user.phone:
                    user.phone = parsed_phone
                if not user.is_verified:
                    user.verify_email()
                user.update_last_login()
                await self.user_repository.update(user)
                log.info("google_oauth_linked", user_id=str(user.id))
            else:
                # 5. Create new user (auto-verified, no password)
                user = User(
                    email=Email(value=email_str),
                    hashed_password="",  # No password for OAuth users
                    first_name=first_name or email_str.split("@")[0],
                    last_name=last_name or "",
                    role=UserRole.STORE_OWNER,
                    status=UserStatus.ACTIVE,  # Auto-verified via Google
                    email_verified_at=datetime.now(UTC),
                    avatar_url=avatar_url,
                    phone=parsed_phone,
                    trial_ends_at=datetime.now(UTC) + timedelta(days=14),
                    auth_provider="google",
                    google_id=google_sub,
                )
                user = await self.user_repository.create(user)
                log.info("google_oauth_registered", user_id=str(user.id))
        else:
            # Existing Google user — just login
            if avatar_url and not user.avatar_url:
                user.avatar_url = avatar_url
            if parsed_phone and not user.phone:
                user.phone = parsed_phone
            user.update_last_login()
            await self.user_repository.update(user)
            log.info("google_oauth_login", user_id=str(user.id))

        # 6. Generate tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)

        return AuthResponseDTO(
            user=UserDTO.from_entity(user),
            tokens=TokenDTO(
                access_token=access_token,
                refresh_token=refresh_token,
            ),
        )
