"""Public beta-invite endpoints.

These power the /accept-invite page in the merchant hub:

- GET  /api/v1/public/beta/invite/{code}  — fetch invitee details + status
- POST /api/v1/public/beta/redeem         — email + password redemption
- POST /api/v1/public/beta/redeem-google  — Google OAuth redemption

A merchant can only enter the platform during beta by redeeming an
admin-issued invite code through this flow. The waitlist's referral_code
is a different column entirely and cannot be used here.

Both redemption endpoints auto-verify the email — clicking the invite
link or signing in with Google already proves email ownership, so we
skip the 6-digit verification step that normal /auth/register users go
through.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_password_service,
    get_token_service,
    get_user_repository,
)
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_auth_cookies
from src.api.v1.schemas import AuthResponse, TokenResponse, UserResponse
from src.api.v1.schemas.public.waitlist import (
    BetaInviteCheckResponse,
    BetaRedeemGoogleRequest,
    BetaRedeemRequest,
)
from src.application.dto.auth import RegisterDTO
from src.application.dto.store import CreateStoreDTO
from src.application.use_cases.auth import RegisterUserUseCase
from src.application.use_cases.stores import CreateStoreUseCase
from src.config import settings
from src.core.entities.user import User, UserRole, UserStatus
from src.core.entities.waitlist import WaitlistEntry, WaitlistStatus
from src.core.exceptions import EntityAlreadyExistsError
from src.core.value_objects.email import Email
from src.infrastructure.external_services import PasswordService, TokenService
from src.infrastructure.repositories import (
    OnboardingRepository,
    StoreRepository,
    UserRepository,
    WaitlistRepository,
)
from src.infrastructure.tenancy.service import TRIAL_LIFETIME_DAYS, TenantService

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _user_response(user) -> UserResponse:
    """Build a UserResponse from either a UserDTO (str phone) or a User entity
    (PhoneNumber phone). Both code paths funnel through here."""
    phone = getattr(user, "phone", None)
    return UserResponse(
        id=str(user.id),
        email=str(user.email),
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=getattr(user, "full_name", f"{user.first_name} {user.last_name}"),
        phone=str(phone) if phone else None,
        role=getattr(user.role, "value", str(user.role)),
        status=getattr(user.status, "value", str(user.status)),
        avatar_url=getattr(user, "avatar_url", None),
        is_verified=getattr(user, "is_verified", False),
        created_at=str(user.created_at),
        updated_at=str(user.updated_at),
        trial_ends_at=str(user.trial_ends_at) if user.trial_ends_at else None,
    )


def _send_welcome_email(email: str, first_name: str | None) -> None:
    """Best-effort welcome-email dispatch. Never blocks the response."""
    try:
        from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
            send_welcome_email_task,
        )

        send_welcome_email_task.delay(
            email=email,
            merchant_name=first_name or "",
        )
    except Exception:
        logger.warning("welcome_email_dispatch_failed", exc_info=True)


async def _create_store_for_user(
    db: AsyncSession,
    *,
    owner_id: UUID,
    store_name: str,
    subdomain: str,
) -> None:
    """Create the store + tenant for a freshly-redeemed beta merchant."""
    store_repo = StoreRepository(db)
    onboarding_repo = OnboardingRepository(db)
    tenant_service = TenantService(db)

    create_store_use_case = CreateStoreUseCase(
        store_repository=store_repo,
        tenant_service=tenant_service,
        onboarding_repository=onboarding_repo,
    )

    await create_store_use_case.execute(
        CreateStoreDTO(name=store_name, subdomain=subdomain),
        owner_id=owner_id,
        plan="beta",
    )


async def _mark_converted(
    waitlist_repo: WaitlistRepository, entry: WaitlistEntry
) -> None:
    """Mark the waitlist entry as CONVERTED."""
    entry.convert()
    await waitlist_repo.update(entry)


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get(
    "/beta/invite/{code}",
    response_model=SuccessResponse[BetaInviteCheckResponse],
    summary="Check beta invite code",
    operation_id="check_beta_invite",
)
async def check_beta_invite(
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Look up an invite code and report its state.

    Returns the invitee's prefill info plus a `status` field:
    - `invited`   — show the redemption form
    - `converted` — already redeemed; frontend should bounce to login
    - `pending`   — the admin hasn't sent the invite yet (rare; race state)

    Returns 404 only when the code is unknown entirely — distinguishing this
    from "already used" lets the frontend recover gracefully on double-clicks.
    """
    repo = WaitlistRepository(db)
    entry = await repo.get_by_invite_code(code)

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invite code",
        )

    return SuccessResponse(
        data=BetaInviteCheckResponse(
            email=entry.email,
            name=entry.name,
            company_name=entry.company_name,
            status=getattr(entry.status, "value", str(entry.status)),
        ),
        message="Invite found",
    )


@router.post(
    "/beta/redeem",
    response_model=SuccessResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Redeem beta invite — email + password",
    operation_id="redeem_beta_invite",
)
async def redeem_beta_invite(
    request: BetaRedeemRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Redeem an admin-issued beta invite with email + password.

    Creates the merchant user (using the email tied to the invite — never
    the user-supplied one), creates their first store with plan="beta",
    marks the waitlist entry as CONVERTED, and returns auth tokens.

    The email is auto-verified: clicking the invite link in their inbox
    proves they own the address, so we skip the 6-digit verification step.
    """
    waitlist_repo = WaitlistRepository(db)
    entry = await waitlist_repo.get_by_invite_code(request.invite_code)

    if not entry or entry.status != WaitlistStatus.INVITED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite code",
        )

    canonical_email = entry.email

    if await user_repo.email_exists(Email(value=canonical_email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An account already exists for this email. Please log in "
                "and contact support to redeem your beta invite."
            ),
        )

    # Step 1 — register the user. We pass email_service=None so the
    # verification email is NOT sent: clicking the invite link already
    # proved email ownership.
    register_use_case = RegisterUserUseCase(
        user_repository=user_repo,
        password_service=password_service,
        token_service=token_service,
        email_service=None,
    )

    register_dto = RegisterDTO(
        email=canonical_email,
        password=request.password,
        first_name=request.first_name,
        last_name=request.last_name,
        phone=request.phone,
    )

    try:
        auth_result = await register_use_case.execute(register_dto)
    except EntityAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email.",
        ) from e

    new_user_id = auth_result.user.id

    # Step 2 — flip the user to verified/active immediately.
    fresh_user = await user_repo.get_by_id(new_user_id)
    if fresh_user and not fresh_user.is_verified:
        fresh_user.verify_email()
        await user_repo.update(fresh_user)

    # Step 3 — create the store
    await _create_store_for_user(
        db,
        owner_id=new_user_id,
        store_name=request.store_name,
        subdomain=request.subdomain,
    )

    # Step 4 — mark the waitlist entry converted
    await _mark_converted(waitlist_repo, entry)

    # Step 5 — fire the welcome email (non-blocking)
    _send_welcome_email(canonical_email, request.first_name)

    logger.info(
        "beta_invite_redeemed",
        extra={
            "waitlist_entry_id": str(entry.id),
            "user_id": str(new_user_id),
            "email": canonical_email,
            "auth": "password",
        },
    )

    set_auth_cookies(
        response,
        auth_result.tokens.access_token,
        auth_result.tokens.refresh_token,
    )

    return SuccessResponse(
        data=AuthResponse(
            user=_user_response(fresh_user or auth_result.user),
            tokens=TokenResponse(
                access_token=auth_result.tokens.access_token,
                refresh_token=auth_result.tokens.refresh_token,
                token_type="bearer",
            ),
        ),
        message="Beta invite redeemed — your store is ready",
    )


@router.post(
    "/beta/redeem-google",
    response_model=SuccessResponse[AuthResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Redeem beta invite — Google OAuth",
    operation_id="redeem_beta_invite_google",
)
async def redeem_beta_invite_google(
    request: BetaRedeemGoogleRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
):
    """Redeem a beta invite via Google OAuth.

    The Google account's email MUST match the waitlist entry's email — a
    forwarded invite cannot be redeemed into a different Google account.
    On success: creates an auto-verified Google-linked user, creates their
    store, marks the invite converted, and returns auth tokens.
    """
    # Step 1 — verify the invite first (cheap reject before contacting Google)
    waitlist_repo = WaitlistRepository(db)
    entry = await waitlist_repo.get_by_invite_code(request.invite_code)

    if not entry or entry.status != WaitlistStatus.INVITED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invite code",
        )

    canonical_email = entry.email.lower()

    # Step 2 — verify the Google ID token
    client_id = settings.google_oauth_client_id
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured.",
        )

    try:
        idinfo = google_id_token.verify_oauth2_token(
            request.id_token,
            google_requests.Request(),
            client_id,
        )
    except Exception as e:
        logger.warning("beta_redeem_google_token_invalid", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        ) from e

    google_email = (idinfo.get("email") or "").lower()
    google_email_verified = idinfo.get("email_verified", False)
    google_sub = idinfo["sub"]
    given_name = idinfo.get("given_name", "") or canonical_email.split("@")[0]
    family_name = idinfo.get("family_name", "")
    avatar_url = idinfo.get("picture")

    if not google_email or not google_email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified",
        )

    # Step 3 — anti-hijack: Google email must match the invite's email
    if google_email != canonical_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This invite was sent to {canonical_email}. Please sign in "
                "with that Google account."
            ),
        )

    # Step 4 — reject if the email already has a user
    if await user_repo.email_exists(Email(value=canonical_email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("An account already exists for this email. Please log in."),
        )

    # Step 5 — create the Google-linked, auto-verified user
    new_user = User(
        email=Email(value=canonical_email),
        hashed_password="",  # OAuth — no password
        first_name=given_name,
        last_name=family_name,
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
        avatar_url=avatar_url,
        trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_LIFETIME_DAYS),
        auth_provider="google",
        google_id=google_sub,
    )
    created_user = await user_repo.create(new_user)

    # Step 6 — create the store
    await _create_store_for_user(
        db,
        owner_id=created_user.id,
        store_name=request.store_name,
        subdomain=request.subdomain,
    )

    # Step 7 — mark waitlist converted
    await _mark_converted(waitlist_repo, entry)

    # Step 8 — fire welcome email (non-blocking)
    _send_welcome_email(canonical_email, given_name)

    # Step 9 — issue auth tokens
    access_token = token_service.create_access_token(created_user)
    refresh_token = token_service.create_refresh_token(created_user)

    logger.info(
        "beta_invite_redeemed",
        extra={
            "waitlist_entry_id": str(entry.id),
            "user_id": str(created_user.id),
            "email": canonical_email,
            "auth": "google",
        },
    )

    set_auth_cookies(response, access_token, refresh_token)

    return SuccessResponse(
        data=AuthResponse(
            user=_user_response(created_user),
            tokens=TokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="bearer",
            ),
        ),
        message="Beta invite redeemed — your store is ready",
    )
