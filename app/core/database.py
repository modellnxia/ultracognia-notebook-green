"""
Gerenciamento do pool de conexões com o PostgreSQL via asyncpg.
Inicializado no startup da aplicação (main.py).
"""

import logging
from typing import AsyncGenerator

import asyncpg

from app.core.settings import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def create_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        statement_cache_size=0,
    )
    logger.info("Pool de conexões PostgreSQL inicializado.")


async def close_pool() -> None:
    if _pool:
        await _pool.close()
        logger.info("Pool de conexões PostgreSQL encerrado.")


async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    if _pool is None:
        logger.error("Pool não inicializado. Chame create_pool() no startup.")
        raise RuntimeError("Pool não inicializado. Chame create_pool() no startup.")
    async with _pool.acquire() as conn:
        yield conn
