import asyncio
import asyncpg
import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from app.core.settings import settings

async def main():
    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        print("Adicionando coluna end_date...")
        await conn.execute("ALTER TABLE notebooks ADD COLUMN IF NOT EXISTS end_date DATE;")
        
        print("Populando end_date com start_date...")
        await conn.execute("UPDATE notebooks SET end_date = start_date WHERE end_date IS NULL;")
        
        print("Definindo NOT NULL...")
        await conn.execute("ALTER TABLE notebooks ALTER COLUMN end_date SET NOT NULL;")
        
        print("Removendo constraint antiga...")
        await conn.execute("ALTER TABLE notebooks DROP CONSTRAINT IF EXISTS notebooks_user_id_start_date_key;")
        
        print("Criando nova constraint de unicidade...")
        try:
            await conn.execute("ALTER TABLE notebooks ADD CONSTRAINT notebooks_user_id_dates_key UNIQUE (user_id, start_date, end_date);")
        except Exception as e:
            if "already exists" not in str(e):
                raise
        print("Migração executada com sucesso!")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
