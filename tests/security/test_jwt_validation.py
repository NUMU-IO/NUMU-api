"""Tests for JWT token validation and security (RS256)."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from src.core.entities.customer import Customer
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.core.value_objects.email import Email
from src.infrastructure.external_services.token_service import TokenService


def _generate_rsa_keypair() -> tuple[str, str]:
    """Generate a throwaway RSA key pair for tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


# Module-level keys so every test in this file shares one fast generation.
_PRIVATE_KEY, _PUBLIC_KEY = _generate_rsa_keypair()
_ALT_PRIVATE_KEY, _ALT_PUBLIC_KEY = _generate_rsa_keypair()


class TestJWTTokenService:
    """Tests for JWT token service security with RS256."""

    @pytest.fixture
    def token_service(self):
        """Create a token service configured for RS256 testing."""
        return TokenService(
            private_key=_PRIVATE_KEY,
            public_key=_PUBLIC_KEY,
            algorithm="RS256",
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

    # -----------------------------------------------------------------
    # Access / Refresh token creation & verification
    # -----------------------------------------------------------------

    def test_create_access_token(self, token_service, sample_user):
        """Test creating a valid access token."""
        token = token_service.create_access_token(sample_user)

        assert token is not None
        assert len(token) > 0

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

    # -----------------------------------------------------------------
    # Signature / key mismatch
    # -----------------------------------------------------------------

    def test_verify_token_with_wrong_key_pair(self, token_service, sample_user):
        """Token signed by a different private key must be rejected."""
        other_service = TokenService(
            private_key=_ALT_PRIVATE_KEY,
            public_key=_ALT_PUBLIC_KEY,
            algorithm="RS256",
        )
        token = other_service.create_access_token(sample_user)

        with pytest.raises(InvalidTokenError):
            token_service.verify_token(token)

    # -----------------------------------------------------------------
    # Expiration
    # -----------------------------------------------------------------

    def test_verify_expired_token(self, token_service, sample_user):
        """Expired token must raise TokenExpiredError."""
        expire = datetime.utcnow() - timedelta(hours=1)
        payload = {
            "sub": str(sample_user.id),
            "email": str(sample_user.email),
            "role": sample_user.role.value,
            "token_type": "access",
            "exp": expire,
            "iat": datetime.utcnow() - timedelta(hours=2),
        }
        token = jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256")

        with pytest.raises(TokenExpiredError):
            token_service.verify_token(token)

    # -----------------------------------------------------------------
    # Malformed / empty tokens
    # -----------------------------------------------------------------

    def test_verify_malformed_token(self, token_service):
        """Malformed token must raise InvalidTokenError."""
        with pytest.raises(InvalidTokenError):
            token_service.verify_token("not.a.valid.token")

    def test_verify_empty_token(self, token_service):
        """Empty token must raise InvalidTokenError."""
        with pytest.raises(InvalidTokenError):
            token_service.verify_token("")

    # -----------------------------------------------------------------
    # decode_token (non-raising variant)
    # -----------------------------------------------------------------

    def test_decode_token_returns_none_for_invalid(self, token_service):
        """decode_token returns None instead of raising."""
        result = token_service.decode_token("invalid-token")
        assert result is None

    def test_decode_token_returns_payload_for_valid(self, token_service, sample_user):
        """decode_token returns payload for a valid token."""
        token = token_service.create_access_token(sample_user)
        result = token_service.decode_token(token)

        assert result is not None
        assert result.user_id == sample_user.id

    # -----------------------------------------------------------------
    # Customer tokens
    # -----------------------------------------------------------------

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
        """User token must fail customer token verification."""
        token = token_service.create_access_token(sample_user)

        with pytest.raises(InvalidTokenError):
            token_service.verify_customer_token(token)

    # -----------------------------------------------------------------
    # Claims validation
    # -----------------------------------------------------------------

    def test_token_contains_correct_claims(self, token_service, sample_user):
        """Token must contain all required claims."""
        token = token_service.create_access_token(sample_user)

        payload = jwt.decode(token, _PUBLIC_KEY, algorithms=["RS256"])

        assert "sub" in payload
        assert "email" in payload
        assert "role" in payload
        assert "token_type" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_token_expiration_time(self, token_service, sample_user):
        """Access token should expire in ~30 minutes."""
        token = token_service.create_access_token(sample_user)

        payload = jwt.decode(token, _PUBLIC_KEY, algorithms=["RS256"])

        exp = datetime.fromtimestamp(payload["exp"])
        iat = datetime.fromtimestamp(payload["iat"])

        delta = exp - iat
        assert 29 <= delta.total_seconds() / 60 <= 31


class TestRS256KeyValidation:
    """Tests for RS256 key validation in Settings."""

    def test_missing_private_key_raises_error(self):
        """Settings must reject RS256 config without a private key."""
        from pydantic import ValidationError

        from src.config.settings import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(
                jwt_algorithm="RS256",
                jwt_private_key="",
                jwt_public_key=_PUBLIC_KEY,
                session_secret_key="session-secret-key-minimum-32-characters",
            )

        assert "JWT_PRIVATE_KEY" in str(exc_info.value)

    def test_missing_public_key_raises_error(self):
        """Settings must reject RS256 config without a public key."""
        from pydantic import ValidationError

        from src.config.settings import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(
                jwt_algorithm="RS256",
                jwt_private_key=_PRIVATE_KEY,
                jwt_public_key="",
                session_secret_key="session-secret-key-minimum-32-characters",
            )

        assert "JWT_PUBLIC_KEY" in str(exc_info.value)

    def test_valid_rs256_keys_pass(self):
        """Valid RSA key pair must pass validation."""
        from src.config.settings import Settings

        s = Settings(
            jwt_algorithm="RS256",
            jwt_private_key=_PRIVATE_KEY,
            jwt_public_key=_PUBLIC_KEY,
            session_secret_key="session-secret-key-minimum-32-characters",
        )

        assert s.jwt_algorithm == "RS256"
        assert s.jwt_private_key == _PRIVATE_KEY
        assert s.jwt_public_key == _PUBLIC_KEY
