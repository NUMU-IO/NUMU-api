
import asyncio
import asyncpg
import os

async def main():
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    dbname = os.getenv("POSTGRES_DB", "numu")
    
    print(f"Connecting to {dbname} at {host}:{port}...")
    
    try:
        conn = await asyncpg.connect(user=user, password=password, host=host, port=port, database=dbname)
        
        print("Fixing 1: Adding missing password_hash column to customers...")
        try:
            # Check if column exists
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = 'customers' 
                    AND column_name = 'password_hash'
                );
            """)
            
            if not exists:
                await conn.execute("ALTER TABLE public.customers ADD COLUMN password_hash VARCHAR(255);")
                print("Column 'password_hash' added.")
            else:
                print("Column 'password_hash' already exists.")
                
            # Also check for other fields that might be missing from that migration
            # accepts_marketing, is_verified, notes, tags
            await conn.execute("ALTER TABLE public.customers ADD COLUMN IF NOT EXISTS accepts_marketing BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE public.customers ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;")
            await conn.execute("ALTER TABLE public.customers ADD COLUMN IF NOT EXISTS notes TEXT;")
            await conn.execute("ALTER TABLE public.customers ADD COLUMN IF NOT EXISTS tags VARCHAR[];")
            print("Verified other customer columns.")
            
        except Exception as e:
            print(f"Error adding column: {e}")

        print("Fixing 2: Setting subdomain for Demo Store...")
        try:
            result = await conn.execute("UPDATE public.stores SET subdomain = 'demo' WHERE slug = 'demo-store' AND subdomain IS NULL;")
            print(f"Update result: {result}")
        except Exception as e:
            print(f"Error updating store: {e}")
            
        await conn.close()
        print("DB Fixes completed.")
        
    except Exception as e:
        print(f"Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
