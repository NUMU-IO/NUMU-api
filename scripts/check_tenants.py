import asyncio
import os
import sys

# Add src to python path
sys.path.append(os.getcwd())

from sqlalchemy import select

from src.infrastructure.database.connection import AsyncSessionLocal, engine
from src.infrastructure.database.models.public.tenant import TenantModel


async def main():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(TenantModel.name, TenantModel.subdomain, TenantModel.id)
            )
            tenants = result.all()

            print("\n----- VALID TENANTS -----")
            if not tenants:
                print("No tenants found in the database.")
            else:
                for name, subdomain, id in tenants:
                    print(f"Name: {name}")
                    print(f"Subdomain: {subdomain}")
                    print(f"ID: {id}")
                    print("-" * 20)
            print("-------------------------\n")
    except Exception as e:
        print(f"Error querying database: {e}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
