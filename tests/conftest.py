"""Pytest configuration and fixtures."""

# Disable rate limiting for tests and set test encryption key before importing anything else
import os

os.environ["RATE_LIMIT_ENABLED"] = "false"
# Set a test encryption key for credential encryption (32 bytes base64 encoded)
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = (
    "dGVzdF9lbmNyeXB0aW9uX2tleV9mb3JfdGVzdGluZzEyMzQ1Njc4OQ=="
)

# Import configuration fixtures
pytest_plugins = [
    "tests.fixtures.configuration_fixtures",
]

import asyncio
from collections.abc import AsyncGenerator, Generator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_db
from src.core.entities.category import Category
from src.core.entities.customer import Customer
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
)
from src.core.entities.product import Product, ProductStatus
from src.core.entities.store import Store, StoreStatus

# Import entities and value objects for fixtures
from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.address import Address
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency, Money
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.connection import Base
from src.infrastructure.external_services.token_service import TokenService
from src.main import app

# Test database URL (use SQLite for tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# =============================================================================
# Event Loop & Database Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


_metadata_patched = False


def _patch_metadata_for_sqlite(metadata):
    """Patch metadata so PostgreSQL-specific features work with SQLite for testing."""
    global _metadata_patched
    if _metadata_patched:
        return
    _metadata_patched = True

    from sqlalchemy import JSON, LargeBinary, Text
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB
    from sqlalchemy.dialects.postgresql.base import TSVECTOR

    for table in metadata.tables.values():
        # Strip schema from tables (e.g. CREATE TABLE public.users → CREATE TABLE users)
        if getattr(table, "schema", None):
            table.schema = None

        for column in table.columns:
            # Strip schema from Enum column types
            if isinstance(column.type, SAEnum) and getattr(column.type, "schema", None):
                column.type.schema = None
            # Replace JSONB with JSON (SQLite doesn't support JSONB)
            if isinstance(column.type, JSONB):
                column.type = JSON()
            # Replace ARRAY with JSON (SQLite doesn't support ARRAY)
            if isinstance(column.type, ARRAY):
                column.type = JSON()
            # Replace BYTEA with LargeBinary (SQLite stores BLOB)
            if isinstance(column.type, BYTEA):
                column.type = LargeBinary()
            # Replace TSVECTOR with Text (SQLite has no full-text-search column type).
            # Also drop the Computed expression — its body references Postgres
            # functions (to_tsvector, setweight, array_to_string) that SQLite
            # can't compile.
            if isinstance(column.type, TSVECTOR):
                column.type = Text()
                column.computed = None
                column.server_default = None

    # Strip schema from ForeignKey references that use schema-qualified names
    for table in metadata.tables.values():
        for fk in table.foreign_keys:
            if fk._colspec and isinstance(fk._colspec, str) and "." in fk._colspec:
                parts = fk._colspec.split(".")
                # If it's schema.table.column, strip the schema part
                if len(parts) == 3:
                    fk._colspec = f"{parts[1]}.{parts[2]}"


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Patch PostgreSQL-specific features for SQLite compatibility
    _patch_metadata_for_sqlite(Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_factory = sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        yield session


# =============================================================================
# HTTP Client Fixtures
# =============================================================================


@pytest_asyncio.fixture(scope="function")
async def client(test_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a test client with overridden dependencies."""

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def sync_client() -> Generator[TestClient, None, None]:
    """Create a synchronous test client."""
    with TestClient(app) as client:
        yield client


# =============================================================================
# Token Service Fixtures
# =============================================================================


def _generate_test_rsa_keypair() -> tuple[str, str]:
    """Generate an RSA key pair for test token signing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


# Generated once per test session to avoid repeated key generation overhead.
_TEST_PRIVATE_KEY, _TEST_PUBLIC_KEY = _generate_test_rsa_keypair()


@pytest.fixture
def token_service() -> TokenService:
    """Create a token service instance for testing (RS256)."""
    return TokenService(
        private_key=_TEST_PRIVATE_KEY,
        public_key=_TEST_PUBLIC_KEY,
        algorithm="RS256",
        access_token_expire_minutes=30,
        refresh_token_expire_days=7,
    )


# =============================================================================
# Value Object Fixtures
# =============================================================================


@pytest.fixture
def sample_email() -> Email:
    """Create a sample Email value object."""
    return Email(value=f"test_{uuid4().hex[:8]}@example.com")


@pytest.fixture
def sample_phone() -> PhoneNumber:
    """Create a sample PhoneNumber value object."""
    return PhoneNumber(value="+201234567890", country_code="EG")


@pytest.fixture
def sample_address() -> Address:
    """Create a sample Address value object."""
    return Address(
        address_line1="123 Main Street",
        city="Cairo",
        state="Cairo Governorate",
        postal_code="11511",
        country="EG",
    )


@pytest.fixture
def sample_money() -> Money:
    """Create a sample Money value object."""
    return Money(amount=Decimal("99.99"), currency=Currency.USD)


# =============================================================================
# Entity Fixtures
# =============================================================================


@pytest.fixture
def sample_user() -> User:
    """Create a sample User entity."""
    return User(
        id=uuid4(),
        email=Email(value=f"user_{uuid4().hex[:8]}@example.com"),
        hashed_password="$2b$12$hashedpasswordfortesting",
        first_name="John",
        last_name="Doe",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
    )


@pytest.fixture
def sample_admin_user() -> User:
    """Create a sample super admin User entity."""
    return User(
        id=uuid4(),
        email=Email(value=f"admin_{uuid4().hex[:8]}@example.com"),
        hashed_password="$2b$12$hashedpasswordfortesting",
        first_name="Admin",
        last_name="User",
        role=UserRole.SUPER_ADMIN,
        status=UserStatus.ACTIVE,
    )


@pytest.fixture
def sample_customer_user() -> User:
    """Create a sample customer User entity."""
    return User(
        id=uuid4(),
        email=Email(value=f"customer_{uuid4().hex[:8]}@example.com"),
        hashed_password="$2b$12$hashedpasswordfortesting",
        first_name="Jane",
        last_name="Customer",
        role=UserRole.CUSTOMER,
        status=UserStatus.ACTIVE,
    )


@pytest.fixture
def sample_store(sample_user: User) -> Store:
    """Create a sample Store entity."""
    return Store(
        id=uuid4(),
        name=f"Test Store {uuid4().hex[:8]}",
        slug=f"test-store-{uuid4().hex[:8]}",
        owner_id=sample_user.id,
        description="A test store for testing purposes",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.USD,
        contact_email="store@example.com",
    )


@pytest.fixture
def sample_customer(sample_store: Store) -> Customer:
    """Create a sample Customer entity."""
    return Customer(
        id=uuid4(),
        store_id=sample_store.id,
        email=Email(value=f"shopper_{uuid4().hex[:8]}@example.com"),
        first_name="Alice",
        last_name="Shopper",
        phone=PhoneNumber(value="+201234567890", country_code="EG"),
        is_verified=True,
    )


@pytest.fixture
def sample_category(sample_store: Store) -> Category:
    """Create a sample Category entity."""
    return Category(
        id=uuid4(),
        store_id=sample_store.id,
        name="Electronics",
        slug="electronics",
        description="Electronic devices and accessories",
    )


@pytest.fixture
def sample_product(sample_store: Store, sample_category: Category) -> Product:
    """Create a sample Product entity."""
    return Product(
        id=uuid4(),
        store_id=sample_store.id,
        name=f"Test Product {uuid4().hex[:8]}",
        slug=f"test-product-{uuid4().hex[:8]}",
        description="A test product for testing",
        price=Money(amount=Decimal("19.99"), currency=Currency.USD),
        sku=f"SKU-{uuid4().hex[:8].upper()}",
        quantity=100,
        status=ProductStatus.ACTIVE,
        category_id=sample_category.id,
    )


@pytest.fixture
def sample_order(
    sample_store: Store, sample_customer: Customer, sample_product: Product
) -> Order:
    """Create a sample Order entity."""
    shipping_address = OrderShippingAddress(
        first_name="Alice",
        last_name="Shopper",
        address_line1="123 Main Street",
        city="Cairo",
        country="EG",
        postal_code="11511",
    )
    line_item = OrderLineItem(
        product_id=sample_product.id,
        product_name=sample_product.name,
        sku=sample_product.sku,
        quantity=2,
        unit_price=sample_product.price,
        total_price=sample_product.price * 2,
    )
    return Order(
        id=uuid4(),
        store_id=sample_store.id,
        customer_id=sample_customer.id,
        order_number=f"ORD-{uuid4().hex[:8].upper()}",
        line_items=[line_item],
        shipping_address=shipping_address,
        subtotal=line_item.total_price,
        total=line_item.total_price,
        status=OrderStatus.PENDING,
    )


# =============================================================================
# Authentication Header Fixtures
# =============================================================================


@pytest.fixture
def authenticated_user_headers(
    sample_user: User, token_service: TokenService
) -> dict[str, str]:
    """Create authentication headers for a regular user."""
    token = token_service.create_access_token(sample_user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def authenticated_admin_headers(
    sample_admin_user: User, token_service: TokenService
) -> dict[str, str]:
    """Create authentication headers for a super admin."""
    token = token_service.create_access_token(sample_admin_user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def authenticated_customer_headers(
    sample_customer: Customer, token_service: TokenService
) -> dict[str, str]:
    """Create authentication headers for a customer."""
    token = token_service.create_customer_access_token(sample_customer)
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Multi-Tenant Fixtures
# =============================================================================


@pytest.fixture
def tenant_a() -> dict[str, Any]:
    """Create tenant A configuration."""
    return {
        "id": uuid4(),
        "subdomain": "tenant-a",
        "name": "Tenant A",
        "schema": "tenant_a",
    }


@pytest.fixture
def tenant_b() -> dict[str, Any]:
    """Create tenant B configuration."""
    return {
        "id": uuid4(),
        "subdomain": "tenant-b",
        "name": "Tenant B",
        "schema": "tenant_b",
    }


@pytest.fixture
def tenant_a_headers(tenant_a: dict[str, Any]) -> dict[str, str]:
    """Create headers with tenant A subdomain."""
    return {"X-Tenant-Subdomain": tenant_a["subdomain"]}


@pytest.fixture
def tenant_b_headers(tenant_b: dict[str, Any]) -> dict[str, str]:
    """Create headers with tenant B subdomain."""
    return {"X-Tenant-Subdomain": tenant_b["subdomain"]}


# =============================================================================
# API Request Data Fixtures
# =============================================================================


@pytest.fixture
def sample_user_data() -> dict[str, Any]:
    """Return sample user registration data."""
    return {
        "email": f"test_{uuid4().hex[:8]}@example.com",
        "password": "TestPassword123!",
        "first_name": "Test",
        "last_name": "User",
    }


@pytest.fixture
def sample_store_data() -> dict[str, Any]:
    """Return sample store creation data."""
    return {
        "name": f"Test Store {uuid4().hex[:8]}",
        "subdomain": f"teststore{uuid4().hex[:8]}",
        "description": "A test store for testing purposes",
        "email": "store@example.com",
        "currency": "USD",
        "country": "US",
    }


@pytest.fixture
def sample_product_data() -> dict[str, Any]:
    """Return sample product creation data."""
    return {
        "name": f"Test Product {uuid4().hex[:8]}",
        "description": "A test product for testing purposes",
        "price": 1999,
        "currency": "USD",
        "sku": f"SKU-{uuid4().hex[:8].upper()}",
        "quantity": 100,
    }


@pytest.fixture
def sample_customer_data() -> dict[str, Any]:
    """Return sample customer registration data."""
    return {
        "email": f"customer_{uuid4().hex[:8]}@example.com",
        "password": "CustomerPassword123!",
        "first_name": "Test",
        "last_name": "Customer",
        "phone": "+201234567890",
    }


@pytest.fixture
def sample_category_data() -> dict[str, Any]:
    """Return sample category creation data."""
    return {
        "name": f"Test Category {uuid4().hex[:8]}",
        "description": "A test category for testing purposes",
        "slug": f"test-category-{uuid4().hex[:8]}",
    }


@pytest.fixture
def sample_order_data(sample_product: Product) -> dict[str, Any]:
    """Return sample order creation data."""
    return {
        "line_items": [
            {
                "product_id": str(sample_product.id),
                "quantity": 2,
            }
        ],
        "shipping_address": {
            "first_name": "Test",
            "last_name": "Customer",
            "address_line1": "123 Test Street",
            "city": "Cairo",
            "country": "EG",
            "postal_code": "11511",
        },
    }
