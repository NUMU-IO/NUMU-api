
import asyncio
import asyncpg
import os

async def main():
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    print(f"Connecting to postgres://{user}:***@{host}:{port}/postgres")
    
    try:
        conn = await asyncpg.connect(user=user, password=password, host=host, port=port, database='postgres')
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'numu'")
        if not exists:
            await conn.execute('CREATE DATABASE numu')
            print("Database 'numu' created successfully.")
        else:
            print("Database 'numu' already exists.")
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
