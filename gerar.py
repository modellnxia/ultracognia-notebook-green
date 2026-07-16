import asyncio
import asyncpg
from app.conversas.gerar_conversas import gerar_conversas_json

# 1. Cria uma função assíncrona principal
async def main():
    await gerar_conversas_json()

# 2. Executa o loop assíncrono para rodar a função
if __name__ == "__main__":
    asyncio.run(main())