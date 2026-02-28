"""Authentication use cases module."""

from src.application.use_cases.auth.change_password import (
    ChangePasswordDTO,
    ChangePasswordUseCase,
)
from src.application.use_cases.auth.forgot_password import ForgotPasswordUseCase
from src.application.use_cases.auth.get_current_user import GetCurrentUserUseCase
from src.application.use_cases.auth.login import LoginUserUseCase
from src.application.use_cases.auth.refresh_token import RefreshTokenUseCase
from src.application.use_cases.auth.register import RegisterUserUseCase
from src.application.use_cases.auth.reset_password import ResetPasswordUseCase
from src.application.use_cases.auth.update_profile import (
    UpdateProfileDTO,
    UpdateProfileUseCase,
    UserProfileDTO,
)
from src.application.use_cases.auth.verify_email import VerifyEmailUseCase

__all__ = [
    "RegisterUserUseCase",
    "LoginUserUseCase",
    "RefreshTokenUseCase",
    "GetCurrentUserUseCase",
    "UpdateProfileUseCase",
    "ChangePasswordUseCase",
    "ForgotPasswordUseCase",
    "ResetPasswordUseCase",
    "UpdateProfileDTO",
    "UserProfileDTO",
    "ChangePasswordDTO",
    "VerifyEmailUseCase",
]
