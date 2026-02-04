"""Tests for Two-Factor Authentication (2FA) flow.

These tests verify the complete 2FA lifecycle:
- Enabling 2FA
- Verifying TOTP codes
- Using backup codes
- Disabling 2FA
- Login flow with 2FA
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.core.entities.two_factor import TwoFactorAuth, TwoFactorMethod, TwoFactorStatus
from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.email import Email
from src.infrastructure.external_services.totp_service import TOTPService
from src.infrastructure.repositories.two_factor_repository import InMemoryTwoFactorRepository
from src.application.use_cases.auth.two_factor import (
    Enable2FAUseCase,
    Verify2FAUseCase,
    Disable2FAUseCase,
)


@pytest.fixture
def totp_service() -> TOTPService:
    """Create a TOTP service instance."""
    return TOTPService()


@pytest.fixture
def two_factor_repo() -> InMemoryTwoFactorRepository:
    """Create an in-memory 2FA repository."""
    return InMemoryTwoFactorRepository()


@pytest.fixture
def mock_user_repo() -> MagicMock:
    """Create a mock user repository."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock()
    repo.update = AsyncMock()
    return repo


@pytest.fixture
def test_user() -> User:
    """Create a test user."""
    return User(
        id=uuid4(),
        email=Email(value="test@example.com"),
        hashed_password="$2b$12$hashedpassword",
        first_name="Test",
        last_name="User",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
    )


@pytest.fixture
def mock_password_service() -> MagicMock:
    """Create a mock password service."""
    service = MagicMock()
    service.verify_password = MagicMock(return_value=True)
    return service


class TestEnable2FA:
    """Tests for enabling 2FA."""

    @pytest.mark.asyncio
    async def test_enable_2fa_returns_secret_and_backup_codes(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that enabling 2FA returns secret, QR URI, and backup codes."""
        mock_user_repo.get_by_id.return_value = test_user

        use_case = Enable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        result = await use_case.execute(test_user.id)

        # Verify secret is returned
        assert result.secret is not None
        assert len(result.secret) == 32  # Base32 secret length

        # Verify provisioning URI is returned
        assert result.provisioning_uri is not None
        assert result.provisioning_uri.startswith("otpauth://totp/")
        assert "NUMU" in result.provisioning_uri

        # Verify 10 backup codes are returned
        assert len(result.backup_codes) == 10
        for code in result.backup_codes:
            assert len(code) == 9  # XXXX-XXXX format
            assert "-" in code

    @pytest.mark.asyncio
    async def test_enable_2fa_creates_pending_entity(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that enabling 2FA creates a pending TwoFactorAuth entity."""
        mock_user_repo.get_by_id.return_value = test_user

        use_case = Enable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        await use_case.execute(test_user.id)

        # Verify entity was created
        entity = await two_factor_repo.get_by_user_id(test_user.id)
        assert entity is not None
        assert entity.status == TwoFactorStatus.PENDING
        assert entity.secret is not None

    @pytest.mark.asyncio
    async def test_enable_2fa_fails_if_already_enabled(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that enabling 2FA fails if already enabled."""
        mock_user_repo.get_by_id.return_value = test_user

        # Pre-create an enabled 2FA entity
        existing = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret="EXISTINGSECRET",
        )
        await two_factor_repo.create(existing)

        use_case = Enable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        from src.application.use_cases.auth.two_factor.enable_2fa import TwoFactorAlreadyEnabledError
        with pytest.raises(TwoFactorAlreadyEnabledError):
            await use_case.execute(test_user.id)


class TestVerify2FA:
    """Tests for verifying 2FA codes."""

    @pytest.mark.asyncio
    async def test_verify_valid_totp_code(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that a valid TOTP code is verified correctly."""
        # Create pending 2FA
        secret = totp_service.generate_secret()
        backup_hashes = [
            totp_service.hash_backup_code(code)
            for code in totp_service.generate_backup_codes(10)
        ]
        
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.PENDING,
            secret=secret,
            backup_codes=backup_hashes,
            backup_codes_remaining=10,
        )
        await two_factor_repo.create(two_factor)

        # Get valid TOTP code
        valid_code = totp_service.get_current_code(secret)

        use_case = Verify2FAUseCase(
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        result = await use_case.execute(
            user_id=test_user.id,
            code=valid_code,
            is_initial_setup=True,
        )

        assert result.verified is True
        assert result.method_used == "totp"

    @pytest.mark.asyncio
    async def test_verify_enables_2fa_on_initial_setup(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that verification enables 2FA during initial setup."""
        secret = totp_service.generate_secret()
        
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.PENDING,
            secret=secret,
            backup_codes=[],
            backup_codes_remaining=0,
        )
        await two_factor_repo.create(two_factor)

        valid_code = totp_service.get_current_code(secret)

        use_case = Verify2FAUseCase(
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        await use_case.execute(
            user_id=test_user.id,
            code=valid_code,
            is_initial_setup=True,
        )

        # Verify 2FA is now enabled
        entity = await two_factor_repo.get_by_user_id(test_user.id)
        assert entity.status == TwoFactorStatus.ENABLED

    @pytest.mark.asyncio
    async def test_verify_invalid_code_fails(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that an invalid code is rejected."""
        secret = totp_service.generate_secret()
        
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret=secret,
            backup_codes=[],
            backup_codes_remaining=0,
        )
        await two_factor_repo.create(two_factor)

        use_case = Verify2FAUseCase(
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        from src.application.use_cases.auth.two_factor.verify_2fa import InvalidTwoFactorCodeError
        with pytest.raises(InvalidTwoFactorCodeError):
            await use_case.execute(
                user_id=test_user.id,
                code="000000",  # Invalid code
                is_initial_setup=False,
            )

    @pytest.mark.asyncio
    async def test_verify_backup_code_works(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that a valid backup code is verified and consumed."""
        secret = totp_service.generate_secret()
        backup_codes = totp_service.generate_backup_codes(10)
        backup_hashes = [totp_service.hash_backup_code(c) for c in backup_codes]
        
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret=secret,
            backup_codes=backup_hashes,
            backup_codes_remaining=10,
        )
        await two_factor_repo.create(two_factor)

        use_case = Verify2FAUseCase(
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        # Use first backup code
        result = await use_case.execute(
            user_id=test_user.id,
            code=backup_codes[0],
            is_initial_setup=False,
        )

        assert result.verified is True
        assert result.method_used == "backup_code"
        assert result.backup_codes_remaining == 9

    @pytest.mark.asyncio
    async def test_backup_code_can_only_be_used_once(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that backup codes are single-use."""
        secret = totp_service.generate_secret()
        backup_codes = totp_service.generate_backup_codes(10)
        backup_hashes = [totp_service.hash_backup_code(c) for c in backup_codes]
        
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret=secret,
            backup_codes=backup_hashes,
            backup_codes_remaining=10,
        )
        await two_factor_repo.create(two_factor)

        use_case = Verify2FAUseCase(
            two_factor_repository=two_factor_repo,
            totp_service=totp_service,
        )

        # Use backup code first time - should work
        await use_case.execute(
            user_id=test_user.id,
            code=backup_codes[0],
            is_initial_setup=False,
        )

        # Use same backup code again - should fail
        from src.application.use_cases.auth.two_factor.verify_2fa import InvalidTwoFactorCodeError
        with pytest.raises(InvalidTwoFactorCodeError):
            await use_case.execute(
                user_id=test_user.id,
                code=backup_codes[0],
                is_initial_setup=False,
            )


class TestDisable2FA:
    """Tests for disabling 2FA."""

    @pytest.mark.asyncio
    async def test_disable_2fa_with_valid_password(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        mock_password_service: MagicMock,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that 2FA can be disabled with valid password."""
        mock_user_repo.get_by_id.return_value = test_user

        # Create enabled 2FA
        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret="TESTSECRET",
            backup_codes=["hash1"],
            backup_codes_remaining=1,
        )
        await two_factor_repo.create(two_factor)

        use_case = Disable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            password_service=mock_password_service,
            totp_service=totp_service,
        )

        result = await use_case.execute(
            user_id=test_user.id,
            password="validpassword",
        )

        assert result.is_enabled is False

    @pytest.mark.asyncio
    async def test_disable_2fa_clears_secret_and_backup_codes(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        mock_password_service: MagicMock,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that disabling 2FA clears all sensitive data."""
        mock_user_repo.get_by_id.return_value = test_user

        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret="TESTSECRET",
            backup_codes=["hash1", "hash2"],
            backup_codes_remaining=2,
        )
        await two_factor_repo.create(two_factor)

        use_case = Disable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            password_service=mock_password_service,
            totp_service=totp_service,
        )

        await use_case.execute(
            user_id=test_user.id,
            password="validpassword",
        )

        # Verify data is cleared
        entity = await two_factor_repo.get_by_user_id(test_user.id)
        assert entity.secret is None
        assert entity.backup_codes == []
        assert entity.backup_codes_remaining == 0

    @pytest.mark.asyncio
    async def test_disable_2fa_fails_with_wrong_password(
        self,
        mock_user_repo: MagicMock,
        two_factor_repo: InMemoryTwoFactorRepository,
        mock_password_service: MagicMock,
        totp_service: TOTPService,
        test_user: User,
    ):
        """Test that disabling 2FA fails with wrong password."""
        mock_user_repo.get_by_id.return_value = test_user
        mock_password_service.verify_password.return_value = False

        two_factor = TwoFactorAuth(
            user_id=test_user.id,
            status=TwoFactorStatus.ENABLED,
            secret="TESTSECRET",
        )
        await two_factor_repo.create(two_factor)

        use_case = Disable2FAUseCase(
            user_repository=mock_user_repo,
            two_factor_repository=two_factor_repo,
            password_service=mock_password_service,
            totp_service=totp_service,
        )

        from src.core.exceptions import InvalidCredentialsError
        with pytest.raises(InvalidCredentialsError):
            await use_case.execute(
                user_id=test_user.id,
                password="wrongpassword",
            )


class TestTwoFactorStatus:
    """Tests for 2FA status checking."""

    @pytest.mark.asyncio
    async def test_user_without_2fa_returns_disabled_status(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
    ):
        """Test that user without 2FA shows disabled status."""
        user_id = uuid4()

        has_2fa = await two_factor_repo.user_has_2fa_enabled(user_id)

        assert has_2fa is False

    @pytest.mark.asyncio
    async def test_user_with_pending_2fa_is_not_enabled(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
    ):
        """Test that pending 2FA is not considered enabled."""
        user_id = uuid4()

        two_factor = TwoFactorAuth(
            user_id=user_id,
            status=TwoFactorStatus.PENDING,
            secret="TESTSECRET",
        )
        await two_factor_repo.create(two_factor)

        has_2fa = await two_factor_repo.user_has_2fa_enabled(user_id)

        assert has_2fa is False

    @pytest.mark.asyncio
    async def test_user_with_enabled_2fa_returns_true(
        self,
        two_factor_repo: InMemoryTwoFactorRepository,
    ):
        """Test that enabled 2FA is correctly detected."""
        user_id = uuid4()

        two_factor = TwoFactorAuth(
            user_id=user_id,
            status=TwoFactorStatus.ENABLED,
            secret="TESTSECRET",
        )
        await two_factor_repo.create(two_factor)

        has_2fa = await two_factor_repo.user_has_2fa_enabled(user_id)

        assert has_2fa is True


class TestBackupCodeGeneration:
    """Tests for backup code generation and format."""

    def test_backup_codes_are_unique(self, totp_service: TOTPService):
        """Test that all generated backup codes are unique."""
        codes = totp_service.generate_backup_codes(100)

        assert len(set(codes)) == 100

    def test_backup_codes_have_correct_format(self, totp_service: TOTPService):
        """Test that backup codes follow XXXX-XXXX format."""
        codes = totp_service.generate_backup_codes(10)

        for code in codes:
            assert len(code) == 9
            assert code[4] == "-"
            assert code[:4].isalnum()
            assert code[5:].isalnum()

    def test_backup_code_verification_is_case_insensitive(self, totp_service: TOTPService):
        """Test that backup code verification ignores case."""
        code = "ABCD-EFGH"
        hashed = totp_service.hash_backup_code(code)

        assert totp_service.verify_backup_code("abcd-efgh", hashed) is True
        assert totp_service.verify_backup_code("ABCD-EFGH", hashed) is True
        assert totp_service.verify_backup_code("AbCd-EfGh", hashed) is True

    def test_backup_code_verification_ignores_dashes(self, totp_service: TOTPService):
        """Test that backup code verification works without dashes."""
        code = "ABCD-EFGH"
        hashed = totp_service.hash_backup_code(code)

        assert totp_service.verify_backup_code("ABCDEFGH", hashed) is True


class TestTOTPCodeVerification:
    """Tests for TOTP code verification."""

    def test_valid_totp_code_is_verified(self, totp_service: TOTPService):
        """Test that valid TOTP code is verified correctly."""
        secret = totp_service.generate_secret()
        code = totp_service.get_current_code(secret)

        assert totp_service.verify_code(secret, code) is True

    def test_invalid_totp_code_is_rejected(self, totp_service: TOTPService):
        """Test that invalid TOTP code is rejected."""
        secret = totp_service.generate_secret()

        assert totp_service.verify_code(secret, "000000") is False
        assert totp_service.verify_code(secret, "999999") is False

    def test_totp_code_with_spaces_is_verified(self, totp_service: TOTPService):
        """Test that TOTP code with spaces is normalized."""
        secret = totp_service.generate_secret()
        code = totp_service.get_current_code(secret)
        spaced_code = f"{code[:3]} {code[3:]}"

        assert totp_service.verify_code(secret, spaced_code) is True

    def test_empty_code_is_rejected(self, totp_service: TOTPService):
        """Test that empty code is rejected."""
        secret = totp_service.generate_secret()

        assert totp_service.verify_code(secret, "") is False
        assert totp_service.verify_code(secret, "   ") is False

    def test_non_numeric_code_is_rejected(self, totp_service: TOTPService):
        """Test that non-numeric code is rejected."""
        secret = totp_service.generate_secret()

        assert totp_service.verify_code(secret, "abcdef") is False
        assert totp_service.verify_code(secret, "12ab34") is False
