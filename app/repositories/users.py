"""
Repository para buscar usuários com atividade no banco de dados.
Usado pelo job de backup diário para descobrir quais usuários
tiveram conversas em uma data específica.
"""

from datetime import date
from typing import List
from uuid import UUID

import asyncpg


class UserRepository:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_users_with_messages_on_date(
        self,
        start_date: date,
    ) -> List[UUID]:
        """
        Retorna a lista de UUIDs de todos os usuários que possuem
        ao menos uma mensagem com status 'ok' na data informada.
        """
        rows = await self.conn.fetch(
            """
            SELECT DISTINCT c.user_id
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.created_at::date = $1
              AND m.status = 'ok'
            """,
            start_date,
        )
        return [row["user_id"] for row in rows]

    async def fetch_users_with_messages(
        self
    ) -> List[UUID]:
        """
        Retorna a lista de UUIDs de todos os usuários que possuem
        ao menos uma mensagem com status 'ok' na data informada.
        """
        rows = await self.conn.fetch(
            """
            SELECT DISTINCT c.user_id
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            """
        )
        return [row["user_id"] for row in rows]
