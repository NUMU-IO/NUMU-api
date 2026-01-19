"""Create a superuser for admin access."""

import asyncio
from getpass import getpass
from uuid import uuid4

from sqlalchemy import text, select

from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.external_services.password_service import password_service
from src.core.entities.user import UserRole, UserStatus


async def create_superuser(name: str, password: str) -> None:
    """Create a superuser in the public schema."""
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        
        email = f"{name}@admin.local"
        
        result = await session.execute(
            select(UserModel).where(UserModel.email == email)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            print(f"❌ User '{name}' already exists!")
            return
        
        hashed_password = password_service.hash_password(password)
        
        user = UserModel(
            id=uuid4(),
            email=email,
            hashed_password=hashed_password,
            first_name=name,
            last_name="Admin",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )
        
        session.add(user)
        await session.commit()
        print(f"✅ Superuser '{name}' created!")
        print(f"   Email: {email}")


def main():
    print("=== Create Superuser ===\n")
    
    name = input("Username: ").strip()
    password = getpass("Password: ")
    
    if not name or not password:
        print("❌ Username and password are required!")
        return
    
    asyncio.run(create_superuser(name, password))


if __name__ == "__main__":
    main()