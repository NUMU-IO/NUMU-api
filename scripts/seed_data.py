"""Seed data script for initial database setup."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.entities.user import UserRole
from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import (
    CategoryModel,
    ProductModel,
    StoreModel,
    UserModel,
)
from src.infrastructure.external_services import password_service


async def seed_database():
    """Seed the database with initial data."""
    print("Starting database seeding...")
    
    async with AsyncSessionLocal() as session:
        # Check if data already exists
        existing_users = await session.execute(
            UserModel.__table__.select().limit(1)
        )
        if existing_users.scalar():
            print("Database already seeded. Skipping...")
            return
        
        # Create super admin
        admin_id = uuid4()
        admin_password = password_service.hash_password("admin123456")
        admin = UserModel(
            id=admin_id,
            email="admin@octyrafiy.com",
            password_hash=admin_password,
            first_name="Super",
            last_name="Admin",
            role=UserRole.SUPER_ADMIN.value,
            is_active=True,
            is_verified=True,
        )
        session.add(admin)
        print(f"Created admin user: admin@octyrafiy.com")
        
        # Create store owner
        owner_id = uuid4()
        owner_password = password_service.hash_password("owner123456")
        owner = UserModel(
            id=owner_id,
            email="owner@example.com",
            password_hash=owner_password,
            first_name="Store",
            last_name="Owner",
            role=UserRole.STORE_OWNER.value,
            is_active=True,
            is_verified=True,
        )
        session.add(owner)
        print(f"Created store owner: owner@example.com")
        
        # Create customer
        customer_id = uuid4()
        customer_password = password_service.hash_password("customer123456")
        customer = UserModel(
            id=customer_id,
            email="customer@example.com",
            password_hash=customer_password,
            first_name="Test",
            last_name="Customer",
            role=UserRole.CUSTOMER.value,
            is_active=True,
            is_verified=True,
        )
        session.add(customer)
        print(f"Created customer: customer@example.com")
        
        # Create sample store
        store_id = uuid4()
        store = StoreModel(
            id=store_id,
            owner_id=owner_id,
            name="Demo Store",
            slug="demo-store",
            description="A sample demo store for testing",
            email="store@example.com",
            currency="USD",
            country="US",
            is_active=True,
            is_verified=True,
        )
        session.add(store)
        print(f"Created store: Demo Store")
        
        # Create categories
        electronics_id = uuid4()
        electronics = CategoryModel(
            id=electronics_id,
            store_id=store_id,
            name="Electronics",
            slug="electronics",
            description="Electronic devices and gadgets",
            is_active=True,
        )
        session.add(electronics)
        
        clothing_id = uuid4()
        clothing = CategoryModel(
            id=clothing_id,
            store_id=store_id,
            name="Clothing",
            slug="clothing",
            description="Fashion and apparel",
            is_active=True,
        )
        session.add(clothing)
        print("Created categories: Electronics, Clothing")
        
        # Create sample products
        products = [
            ProductModel(
                id=uuid4(),
                store_id=store_id,
                category_id=electronics_id,
                name="Wireless Headphones",
                slug="wireless-headphones",
                description="High-quality wireless headphones with noise cancellation",
                price=9999,  # $99.99 in cents
                currency="USD",
                sku="WH-001",
                quantity=50,
                images=["https://example.com/headphones.jpg"],
                is_active=True,
            ),
            ProductModel(
                id=uuid4(),
                store_id=store_id,
                category_id=electronics_id,
                name="Smart Watch",
                slug="smart-watch",
                description="Feature-rich smartwatch with health tracking",
                price=19999,  # $199.99 in cents
                currency="USD",
                sku="SW-001",
                quantity=30,
                images=["https://example.com/smartwatch.jpg"],
                is_active=True,
                is_featured=True,
            ),
            ProductModel(
                id=uuid4(),
                store_id=store_id,
                category_id=clothing_id,
                name="Classic T-Shirt",
                slug="classic-t-shirt",
                description="Comfortable cotton t-shirt",
                price=2499,  # $24.99 in cents
                currency="USD",
                sku="TS-001",
                quantity=100,
                images=["https://example.com/tshirt.jpg"],
                is_active=True,
            ),
        ]
        
        for product in products:
            session.add(product)
        print(f"Created {len(products)} sample products")
        
        # Commit all changes
        await session.commit()
        print("Database seeding completed successfully!")
        
        # Print login credentials
        print("\n" + "=" * 50)
        print("TEST ACCOUNTS:")
        print("=" * 50)
        print("Admin:    admin@octyrafiy.com / admin123456")
        print("Owner:    owner@example.com / owner123456")
        print("Customer: customer@example.com / customer123456")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(seed_database())
