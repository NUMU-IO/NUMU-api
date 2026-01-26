"""Tests for JWT token validation and security."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from jose import jwt

from src.core.entities.user import User, UserRole, UserStatus
from src.core.entities.customer import Customer
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.core.value_objects.email import Email
from src.infrastructure.external_services.token_service import TokenService


class TestJWTTokenService:
    """Tests for JWT token service security."""

    @pytest.fixture
    def token_service(self):
        """Create a token service for testing."""
        return TokenService(
            secret_key="test-secret-key-for-testing-minimum-32-chars",
            algorithm="HS256",
            access_token_expire_minutes=30,
            refresh_token_expire_days=7,
        )

    @pytest.fixture
    def sample_user(self):
        """Create a sample user for testing."""
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
    def sample_customer(self):
        """Create a sample customer for testing."""
        return Customer(
            id=uuid4(),
            store_id=uuid4(),
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )

    def test_create_access_token(self, token_service, sample_user):
        """Test creating a valid access token."""
        token = token_service.create_access_token(sample_user)

        assert token is not None
        assert len(token) > 0

        # Verify token can be decoded
        payload = token_service.verify_token(token)
        assert payload.user_id == sample_user.id
        assert payload.email == str(sample_user.email)
        assert payload.role == sample_user.role.value
        assert payload.token_type == "access"

    def test_create_refresh_token(self, token_service, sample_user):
        """Test creating a valid refresh token."""
        token = token_service.create_refresh_token(sample_user)

        assert token is not None
        payload = token_service.verify_token(token)
        assert payload.token_type == "refresh"

    def test_verify_token_with_invalid_signature(self, token_service, sample_user):
        """Test that token with invalid signature is rejected."""
        # Create token with different secret
        other_service = TokenService(
            secret_key="different-secret-key-for-testing-32-chars",
            algorithm="HS256",
        )
        token = other_service.create_access_token(sample_user)

        # Should raise InvalidTokenError
        with pytest.raises(InvalidTokenError):
            token_service.verify_token(token)

    def test_verify_expired_token(self, token_service, sample_user):
        """Test that expired token raises TokenExpiredError."""
        # Create token that's already expired
        expire = datetime.utcnow() - timedelta(hours=1)
        payload = {
            "sub": str(sample_user.id),
            "email": str(sample_user.email),
            "role": sample_user.role.value,
            "token_type": "access",
            "exp": expire,
            "iat": datetime.utcnow() - timedelta(hours=2),
        }
        token = jwt.encode(
            payload,
            "test-secret-key-for-testing-minimum-32-chars",
            algorithm="HS256",
        )

        with pytest.raises(TokenExpiredError):
            token_service.verify_token(token)

    def test_verify_malformed_token(self, token_service):
        """Test that malformed token raises InvalidTokenError."""
        with pytest.raises(InvalidTokenError):
            token_service.verify_token("not.a.valid.token")

    def test_verify_empty_token(self, token_service):
        """Test that empty token raises InvalidTokenError."""
        with pytest.raises(InvalidTokenError):
            token_service.verify_token("")

    def test_decode_token_returns_none_for_invalid(self, token_service):
        """Test decode_token returns None instead of raising."""
        result = token_service.decode_token("invalid-token")
        assert result is None

    def test_decode_token_returns_payload_for_valid(self, token_service, sample_user):
        """Test decode_token returns payload for valid token."""
        token = token_service.create_access_token(sample_user)
        result = token_service.decode_token(token)

        assert result is not None
        assert result.user_id == sample_user.id

    def test_customer_access_token(self, token_service, sample_customer):
        """Test creating a valid customer access token."""
        token = token_service.create_customer_access_token(sample_customer)

        assert token is not None
        payload = token_service.verify_customer_token(token)
        assert payload.customer_id == sample_customer.id
        assert payload.store_id == sample_customer.store_id
        assert payload.email == str(sample_customer.email)

    def test_customer_refresh_token(self, token_service, sample_customer):
        """Test creating a valid customer refresh token."""
        token = token_service.create_customer_refresh_token(sample_customer)

        assert token is not None
        payload = token_service.verify_customer_token(token)
        assert payload.token_type == "refresh"

    def test_user_token_fails_customer_verify(self, token_service, sample_user):
        """Test that user token fails customer token verification."""
        token = token_service.create_access_token(sample_user)

        with pytest.raises(InvalidTokenError):
            token_service.verify_customer_token(token)

    def test_token_contains_correct_claims(self, token_service, sample_user):
        """Test token contains all required claims."""
        token = token_service.create_access_token(sample_user)

        # Decode without verification to check claims
        payload = jwt.decode(
            token,
            "test-secret-key-for-testing-minimum-32-chars",
            algorithms=["HS256"],
        )

        assert "sub" in payload
        assert "email" in payload
        assert "role" in payload
        assert "token_type" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_token_expiration_time(self, token_service, sample_user):
        """Test token expiration is set correctly."""
        token = token_service.create_access_token(sample_user)

        payload = jwt.decode(
            token,
            "test-secret-key-for-testing-minimum-32-chars",
            algorithms=["HS256"],
        )

        exp = datetime.fromtimestamp(payload["exp"])
        iat = datetime.fromtimestamp(payload["iat"])

        # Access token should expire in ~30 minutes
        delta = exp - iat
        assert 29 <= delta.total_seconds() / 60 <= 31


class TestJWTSecretValidation:
    """Tests for JWT secret validation in settings."""

    def test_short_secret_raises_error(self):
        """Test that short JWT secret raises validation error."""
        from pydantic import ValidationError
        from src.config.settings import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(
                jwt_secret_key="short",
                database_url="postgresql+asyncpg://user:pass@localhost/db",
                session_secret_key="session-secret-key-minimum-32-characters",
            )

        assert "JWT_SECRET_KEY" in str(exc_info.value) or "32 characters" in str(exc_info.value)

    def test_valid_secret_passes(self):
        """Test that valid JWT secret passes validation."""
        from src.config.settings import Settings

        # Should not raise
        settings = Settings(
            jwt_secret_key="this-is-a-valid-jwt-secret-key-32-plus-chars",
            database_url="postgresql+asyncpg://user:pass@localhost/db",
            session_secret_key="session-secret-key-minimum-32-characters",
        )

        assert len(settings.jwt_secret_key) >= 32
