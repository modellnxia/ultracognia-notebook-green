"""
Acesso a dados da Theme Library (Fatia A, 2026-08-24) — temas de marca
nomeados e reaproveitáveis por `client_id`, pra um job não depender do LLM
reinventar a paleta a cada geração (ver `structure.py::_fetch_fixed_theme`).
Mesmo estilo de `repository.py`: SQL cru via asyncpg, sem ORM. Ver schema.sql.
"""

import json
from typing import Optional
from uuid import UUID

import asyncpg


class ThemeRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def create_theme(
        self,
        name: str,
        palette: dict[str, str],
        font_stack: str,
        client_id: Optional[UUID] = None,
        logo_url: Optional[str] = None,
    ) -> asyncpg.Record:
        """
        Cria ou atualiza (upsert por `client_id` + `name`) um tema nomeado —
        reenviar o mesmo nome pro mesmo cliente atualiza o tema existente em
        vez de duplicar, útil pra ajustar a paleta de um cliente sem precisar
        saber o `id` gerado antes.

        Nota: com `client_id IS NULL`, o Postgres não considera duas linhas
        com `client_id` nulo como conflitantes entre si (NULL != NULL em
        constraint única) — nesse caso específico (tema sem cliente) o
        upsert vira sempre um INSERT novo, não atualiza. Não é um problema
        pro uso real (tema sempre é de um `client_id`), só uma nuance a
        documentar.
        """
        return await self.conn.fetchrow(
            """
            INSERT INTO deck_themes (client_id, name, palette, font_stack, logo_url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (client_id, name) DO UPDATE
                SET palette = EXCLUDED.palette,
                    font_stack = EXCLUDED.font_stack,
                    logo_url = EXCLUDED.logo_url,
                    updated_at = now()
            RETURNING id, client_id, name, palette, font_stack, logo_url, created_at, updated_at
            """,
            client_id,
            name,
            json.dumps(palette),
            font_stack,
            logo_url,
        )

    async def get_theme(self, theme_id: UUID) -> Optional[asyncpg.Record]:
        return await self.conn.fetchrow(
            "SELECT * FROM deck_themes WHERE id = $1", theme_id
        )

    async def list_themes(self, client_id: Optional[UUID] = None) -> list[asyncpg.Record]:
        if client_id is not None:
            return await self.conn.fetch(
                "SELECT * FROM deck_themes WHERE client_id = $1 ORDER BY name", client_id
            )
        return await self.conn.fetch("SELECT * FROM deck_themes ORDER BY name")
