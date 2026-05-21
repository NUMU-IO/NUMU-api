"""Authenticated demo routes — requires a demo session cookie.

POST /api/v1/demo/convert — promote demo tenant to real 30-day trial.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.services import (
    get_email_service,
    get_password_service,
    get_token_service,
)
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_auth_cookies
from src.api.v1.schemas.public.demo import ConvertDemoRequest, ConvertDemoResponse
from src.application.use_cases.demo.convert_demo import ConvertDemoUseCase
from src.core.exceptions import EntityAlreadyExistsError, ValidationError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/demo/convert",
    response_model=SuccessResponse[ConvertDemoResponse],
    status_code=status.HTTP_200_OK,
    summary="Convert demo tenant to real account (30-day trial)",
    operation_id="convert_demo",
)
async def convert_demo(
    request: ConvertDemoRequest,
    response: Response,
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    password_service: Annotated[object, Depends(get_password_service)],
    token_service: Annotated[object, Depends(get_token_service)],
):
    """Promote the current demo tenant in-place to a real account.

    No payment is collected here — the user lands in a 30-day Trial.
    The seeded fake data is wiped; anything the user added themselves
    during the demo is preserved. Fresh tokens are issued for the new
    real user and set as cookies.
    """
    try:
        email_service = get_email_service()
    except Exception:
        email_service = None

    use_case = ConvertDemoUseCase(
        db=db,
        password_service=password_service,
        token_service=token_service,
        email_service=email_service,
    )

    try:
        result = await use_case.execute(
            demo_user_id=user_id,
            email=request.email,
            password=request.password,
            first_name=request.first_name,
            last_name=request.last_name,
            store_name=request.store_name,
            subdomain=request.subdomain,
            phone=request.phone,
        )
    except EntityAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception:
        logger.exception("demo_convert_failed")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not convert demo. Please try again.",
        )

    # Set fresh auth cookies for the new real user
    set_auth_cookies(response, result.access_token, result.refresh_token)

    return SuccessResponse(
        data=ConvertDemoResponse(
            tenant_id=result.tenant_id,
            subdomain=result.subdomain,
            message="Welcome to NUMU! Your 30-day trial has started. Check your email to verify your account.",
        ),
        message="Demo converted to trial account",
    )
