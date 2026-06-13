from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg


class NotebookRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def get_notebook_by_user_and_date_range(
        self, user_id: UUID, start_date: date, end_date: date
    ) -> Optional[asyncpg.Record]:
        """
        Busca um notebook gerado previamente para um usuário em uma data específica.
        Retorna None se não existir.
        """
        return await self.conn.fetchrow(
            """
            SELECT
                notebook_id,
                notebook_title,
                report_content,
                report_path
            FROM notebooks
            WHERE user_id = $1 AND start_date = $2 AND end_date = $3
            LIMIT 1
            """,
            user_id,
            start_date,
            end_date,
        )
    
    async def get_notebook_by_user(
        self, user_id: UUID
    ) -> Optional[asyncpg.Record]:
        """
        Busca um notebook gerado previamente para um usuário em uma data específica.
        Retorna None se não existir.
        """
        return await self.conn.fetchrow(
            """
            SELECT
                notebook_id,
                notebook_title,
                report_content,
                report_path
            FROM notebooks
            WHERE user_id = $1 
            LIMIT 1
            """,
            user_id
        )
    
    async def save_notebook_id(
        self,
        user_id: UUID,
        notebook_id: str,
        notebook_title: str,
        end_date: date,
    ) -> None:
        """
        Persiste o notebook_id no banco logo após a criação no NotebookLM,
        antes de o relatório ser gerado. report_content e report_path ficam NULL.
        """
        await self.conn.execute(
            """
            INSERT INTO notebooks (
                user_id,
                notebook_id,
                notebook_title,
                end_date
            ) VALUES ($1, $2, $3, $4)
            """,
            user_id,
            notebook_id,
            notebook_title,
            end_date,
        )

    async def update_notebook_report(
        self,
        user_id: UUID,
        start_date: date,
        report_content: str,
        report_path: str,
    ) -> None:
        """
        Atualiza o registro existente com o conteúdo do relatório após a geração.
        """
        await self.conn.execute(
            """
            UPDATE notebooks
            SET report_content = $1, report_path = $2
            WHERE user_id = $3 AND start_date = $4
            """,
            report_content,
            report_path,
            user_id,
            start_date,
        )

    async def save_notebook(
        self,
        user_id: UUID,
        notebook_id: str,
        notebook_title: str,
        start_date: date,
        end_date: date,
        report_content: str,
        report_path: str,
    ) -> None:
        """
        Salva ou atualiza um notebook completo (com relatório) no banco de dados.
        Mantido para compatibilidade com fluxos que geram tudo de uma vez.
        """
        await self.conn.execute(
            """
            INSERT INTO notebooks (
                user_id,
                notebook_id,
                notebook_title,
                start_date,
                end_date,
                report_content,
                report_path
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, start_date, end_date) DO UPDATE SET
                notebook_id = EXCLUDED.notebook_id,
                notebook_title = EXCLUDED.notebook_title,
                report_content = EXCLUDED.report_content,
                report_path = EXCLUDED.report_path,
                created_at = CURRENT_TIMESTAMP
            """,
            user_id,
            notebook_id,
            notebook_title,
            start_date,
            end_date,
            report_content,
            report_path,
        )

    async def update_notebook_report_by_id(
        self,
        notebook_id: str,
        report_content: str,
        report_path: str,
    ) -> None:
        """
        Atualiza o registro existente com o conteúdo do relatório pelo notebook_id.
        """
        await self.conn.execute(
            """
            UPDATE notebooks
            SET report_content = $1, report_path = $2
            WHERE notebook_id = $3
            """,
            report_content,
            report_path,
            notebook_id,
        )
