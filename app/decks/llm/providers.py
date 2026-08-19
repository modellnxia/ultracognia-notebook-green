"""
Provedores de LLM — lê a tabela `providers` já existente na plataforma
(compartilhada com outros serviços, achado em 2026-08-17 — ver CLAUDE.md)
em vez de inventar configuração nova. `priority` define ordem de fallback.
"""

import asyncpg


async def list_active_providers(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT name, url, priority FROM providers WHERE is_active = true ORDER BY priority"
    )
