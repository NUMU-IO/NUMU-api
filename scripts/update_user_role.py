"""Script to update user role."""
import asyncio
from sqlalchemy import text
from src.infrastructure.database.connection import AsyncSessionLocal


async def check_enum():
    async with AsyncSessionLocal() as session:
        # Check what enum values exist
        result = await session.execute(
            text("SELECT enumlabel FROM pg_enum WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'userrole')")
        )
        labels = result.fetchall()
        print("Existing enum values:", [r[0] for r in labels])

        # Check current user role
        result2 = await session.execute(
            text("SELECT id, email, role FROM public.users WHERE email = 'admin@numu.io'")
        )
        user = result2.fetchone()
        if user:
            print(f"Current user: id={user[0]}, email={user[1]}, role={user[2]}")


if __name__ == "__main__":
    asyncio.run(check_enum())
