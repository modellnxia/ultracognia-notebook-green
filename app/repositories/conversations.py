"""
Repository para buscar mensagens do banco de dados.
Estrutura espelhada do projeto ultracognia-frontend-green.

Tabelas relevantes:
  - conversations: id, user_id, title, status, last_message_at
  - messages:      id, conversation_id, role, content, created_at, status
"""

from datetime import date
from typing import List
from uuid import UUID

import asyncpg


class ConversationMessageRepository:

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_messages_by_user_and_date_range(
        self,
        user_id: UUID,
        end_date: date,
    ) -> List[asyncpg.Record]:
        """
        Retorna todas as mensagens (role + content) de todas as conversas
        de um usuário em uma data específica, ordenadas cronologicamente.

        A junção passa por conversations para garantir que só buscamos
        mensagens que pertencem ao usuário informado.
        """
        return await self.conn.fetch(
            """
            SELECT
                m.role,
                m.content,
                m.created_at,
                c.title AS conversation_title
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE c.user_id = $1
              AND m.created_at::date <= $2
              AND m.status = 'ok'
            ORDER BY m.created_at ASC
            """,
            user_id,
            end_date,
        )
