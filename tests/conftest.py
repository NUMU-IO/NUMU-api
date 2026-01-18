"""Pytest configuration and fixtures."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.dependencies import get_db
from src.config import settings
from src.infrastructure.database.connection import Base
from src.main import app

# Test database URL (use SQLite for tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

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


# Sample data fixtures
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
