from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg


class NotebookRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def get_notebook_by_user_and_date(
        self, user_id: UUID, target_date: date
    ) -> Optional[asyncpg.Record]:
        """
        Busca um notebook gerado previamente para um usuário em uma data específica.
        """
        return await self.conn.fetchrow(
            """
            SELECT 
                notebook_id,
                notebook_title,
                report_content,
                report_path
            FROM notebooks
            WHERE user_id = $1 AND target_date = $2
            LIMIT 1
            """,
            user_id,
            target_date,
        )

    async def save_notebook(
        self,
        user_id: UUID,
        notebook_id: str,
        notebook_title: str,
        target_date: date,
        report_content: str,
        report_path: str,
    ) -> None:
        """
        Salva um notebook no banco de dados.
        """
        await self.conn.execute(
            """
            INSERT INTO notebooks (
                user_id, 
                notebook_id, 
                notebook_title, 
                target_date, 
                report_content, 
                report_path
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, target_date) DO UPDATE SET
                notebook_id = EXCLUDED.notebook_id,
                notebook_title = EXCLUDED.notebook_title,
                report_content = EXCLUDED.report_content,
                report_path = EXCLUDED.report_path,
                created_at = CURRENT_TIMESTAMP
            """,
            user_id,
            notebook_id,
            notebook_title,
            target_date,
            report_content,
            report_path,
        )
