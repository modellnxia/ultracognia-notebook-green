"""
Gerenciamento do pool de conexões com o PostgreSQL via asyncpg.
Inicializado no startup da aplicação (main.py).
"""

import logging
import ssl
import asyncio
import time
from typing import AsyncGenerator

import asyncpg

from app.core.settings import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _with_connect_retry(
    connect_coro_factory,
    max_wait_seconds: float,
    retry_interval: float,
    label: str,
):
    """
    Executa `connect_coro_factory()` repetidamente até obter sucesso, tolerando
    até `max_wait_seconds` de falhas (ex.: instância AWS/Supabase ainda subindo)
    antes de desistir e propagar o erro real (credencial inválida, host errado etc.).
    """
    start = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await connect_coro_factory()
            logger.info("Conexão com %s estabelecida (tentativa %d).", label, attempt)
            return result
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            elapsed = time.monotonic() - start
            if elapsed >= max_wait_seconds:
                logger.error(
                    "Falha ao conectar a %s após %.0fs (tentativa %d) — desistindo.",
                    label, elapsed, attempt,
                )
                raise
            logger.warning(
                "Falha ao conectar a %s (tentativa %d, %.0fs desde o início): %s — "
                "nova tentativa em %.0fs",
                label, attempt, elapsed, exc, retry_interval,
            )
            await asyncio.sleep(retry_interval)


async def create_pool(max_wait_seconds: float = 90.0, retry_interval: float = 5.0) -> None:
    global _pool
    _pool = await _with_connect_retry(
        lambda: asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=2,
            max_size=10,
            ssl=_ssl_context(),
            statement_cache_size=0,
            timeout=10,
        ),
        max_wait_seconds,
        retry_interval,
        label="PostgreSQL (pool)",
    )


async def connect_with_retry(
    max_wait_seconds: float = 90.0, retry_interval: float = 5.0
) -> asyncpg.Connection:
    """
    Conexão avulsa (fora do pool do FastAPI) com o mesmo retry/backoff de
    `create_pool()`. Usada pelo job de backup, que abre sua própria conexão
    porque o pool do FastAPI não é acessível fora do contexto de requisição.
    """
    return await _with_connect_retry(
        lambda: asyncpg.connect(dsn=settings.DATABASE_URL, ssl=_ssl_context(), timeout=10),
        max_wait_seconds,
        retry_interval,
        label="PostgreSQL (conexão avulsa)",
    )


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
