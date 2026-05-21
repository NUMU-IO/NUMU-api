"""Idempotent seed data for staging environment.

Usage:
    python scripts/seed_staging.py

Creates:
    - Tenant: "Staging Test Tenant" (subdomain: staging-test)
    - User: staging-owner@numueg.app (role: STORE_OWNER)
    - Store: "Staging Test Store" (slug: staging-test-store)

Safe to run multiple times - skips records that already exist.
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from src.core.entities.store import StoreStatus
from src.core.entities.user import UserRole, UserStatus
from src.core.value_objects.money import Currency
from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import (
    StoreModel,
    TenantModel,
    UserModel,
)
from src.infrastructure.external_services import password_service


async def seed_staging():
    """Seed staging environment with test tenant, user, and store."""
    print("=" * 60)
    print("  NUMU - Staging Seed Data")
    print("=" * 60)
    print()

    async with AsyncSessionLocal() as session:
        created_any = False

        # ── Check/create user ───────────────────────────────────
        user_email = "staging-owner@numueg.app"
        existing_user = await session.execute(
            select(UserModel).where(UserModel.email == user_email)
        )
        user = existing_user.scalar_one_or_none()

        if user:
            print(f"  User '{user_email}' already exists (id={user.id}), skipping.")
            owner_id = user.id
        else:
            owner_id = uuid4()
            hashed_pw = password_service.hash_password("staging123456")
            user = UserModel(
                id=owner_id,
                email=user_email,
                hashed_password=hashed_pw,
                first_name="Staging",
                last_name="Owner",
                role=UserRole.STORE_OWNER,
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
            print(f"  Created user: {user_email} (id={owner_id})")
            created_any = True

        # ── Check/create tenant ─────────────────────────────────
        subdomain = "staging-test"
        existing_tenant = await session.execute(
            select(TenantModel).where(TenantModel.subdomain == subdomain)
        )
        tenant = existing_tenant.scalar_one_or_none()

        if tenant:
            print(f"  Tenant '{subdomain}' already exists (id={tenant.id}), skipping.")
            tenant_id = tenant.id
        else:
            tenant_id = uuid4()
            tenant = TenantModel(
                id=tenant_id,
                name="Staging Test Tenant",
                subdomain=subdomain,
                owner_id=owner_id,
                plan="pro",
                is_active=True,
            )
            session.add(tenant)
            await session.flush()
            print(f"  Created tenant: Staging Test Tenant (id={tenant_id})")
            created_any = True

        # ── Check/create store ──────────────────────────────────
        store_slug = "staging-test-store"
        existing_store = await session.execute(
            select(StoreModel).where(StoreModel.slug == store_slug)
        )
        store = existing_store.scalar_one_or_none()

        if store:
            print(f"  Store '{store_slug}' already exists (id={store.id}), skipping.")
        else:
            store_id = uuid4()
            store = StoreModel(
                id=store_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
                name="Staging Test Store",
                slug=store_slug,
                subdomain=subdomain,
                description="Staging environment test store",
                status=StoreStatus.ACTIVE,
                default_currency=Currency.EGP,
                default_language="en",
                contact_email=user_email,
            )
            session.add(store)
            print(f"  Created store: Staging Test Store (id={store_id})")
            created_any = True

        # ── Commit ──────────────────────────────────────────────
        await session.commit()

        print()
        print("-" * 60)
        if created_any:
            print("  Staging seed completed - new records created.")
        else:
            print("  Staging seed completed - all records already existed.")
        print("-" * 60)
        print()
        print("  STAGING TEST ACCOUNT:")
        print(f"    Email:     {user_email}")
        print("    Password:  staging123456")
        print(f"    Subdomain: {subdomain}")
        print()


if __name__ == "__main__":
    asyncio.run(seed_staging())
