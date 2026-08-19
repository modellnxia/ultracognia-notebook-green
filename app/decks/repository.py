"""
Acesso a dados do módulo de decks — SQL cru via asyncpg, mesmo estilo do
resto do projeto (sem ORM). Ver schema.sql para a estrutura das tabelas.
"""

from typing import Optional
from uuid import UUID

import asyncpg

STEP_ORDER: list[str] = ["structure", "assets", "render", "qa"]


class DeckJobRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def create_job(
        self, user_id: UUID, editorial_text: str, client_id: Optional[UUID] = None
    ) -> asyncpg.Record:
        """Cria o job (com o editorial colado pelo usuário) e já grava as etapas como 'pending', em uma transação."""
        async with self.conn.transaction():
            job = await self.conn.fetchrow(
                """
                INSERT INTO deck_jobs (user_id, client_id, editorial_text, status)
                VALUES ($1, $2, $3, 'pending')
                RETURNING id, user_id, client_id, editorial_text, status, cost_cents, created_at
                """,
                user_id,
                client_id,
                editorial_text,
            )
            for step in STEP_ORDER:
                await self.conn.execute(
                    """
                    INSERT INTO deck_job_steps (job_id, step, status)
                    VALUES ($1, $2, 'pending')
                    """,
                    job["id"],
                    step,
                )
            return job

    async def get_job(self, job_id: UUID) -> Optional[asyncpg.Record]:
        return await self.conn.fetchrow(
            "SELECT * FROM deck_jobs WHERE id = $1", job_id
        )

    async def get_steps(self, job_id: UUID) -> list[asyncpg.Record]:
        rows = await self.conn.fetch(
            "SELECT * FROM deck_job_steps WHERE job_id = $1 ORDER BY created_at",
            job_id,
        )
        # Ordena pela ordem lógica do pipeline, não pela ordem de criação
        # (que já é a mesma aqui, mas não custa deixar explícito e à prova
        # de reordenação futura do STEP_ORDER).
        order_index = {step: i for i, step in enumerate(STEP_ORDER)}
        return sorted(rows, key=lambda r: order_index.get(r["step"], 99))

    async def claim_next_pending_step(self) -> Optional[asyncpg.Record]:
        """
        Pega a etapa 'pending' mais antiga de qualquer job e marca como
        'running' atomicamente (FOR UPDATE SKIP LOCKED — segura contra dois
        pollers rodando ao mesmo tempo pegarem a mesma etapa).
        """
        async with self.conn.transaction():
            row = await self.conn.fetchrow(
                """
                SELECT * FROM deck_job_steps
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if row is None:
                return None
            await self.conn.execute(
                """
                UPDATE deck_job_steps
                SET status = 'running', attempt = attempt + 1, updated_at = now()
                WHERE id = $1
                """,
                row["id"],
            )
            await self.conn.execute(
                "UPDATE deck_jobs SET status = 'running', updated_at = now() "
                "WHERE id = $1 AND status = 'pending'",
                row["job_id"],
            )
            return row

    async def mark_step_completed(
        self,
        step_id: UUID,
        output_ref: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_cents: int = 0,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE deck_job_steps
            SET status = 'completed', output_ref = $2, input_tokens = $3,
                output_tokens = $4, cost_cents = $5, updated_at = now()
            WHERE id = $1
            """,
            step_id,
            output_ref,
            input_tokens,
            output_tokens,
            cost_cents,
        )

    async def mark_step_failed(self, step_id: UUID, error: str) -> None:
        await self.conn.execute(
            """
            UPDATE deck_job_steps
            SET status = 'failed', error = $2, updated_at = now()
            WHERE id = $1
            """,
            step_id,
            error,
        )

    async def finalize_job_if_all_steps_done(self, job_id: UUID) -> None:
        """
        Se todas as etapas do job estão 'completed', marca o job como
        'completed' e soma o custo total. Se alguma falhou definitivamente,
        marca o job como 'failed'.
        """
        steps = await self.get_steps(job_id)
        statuses = {s["status"] for s in steps}

        if statuses == {"completed"}:
            total_cost = sum(s["cost_cents"] for s in steps)
            await self.conn.execute(
                "UPDATE deck_jobs SET status = 'completed', cost_cents = $2, "
                "updated_at = now() WHERE id = $1",
                job_id,
                total_cost,
            )
        elif "failed" in statuses:
            await self.conn.execute(
                "UPDATE deck_jobs SET status = 'failed', updated_at = now() "
                "WHERE id = $1",
                job_id,
            )

    async def enforce_cost_cap(self, job_id: UUID, max_cost_cents: int) -> bool:
        """
        Disjuntor de custo: soma o gasto do job até agora; se estourou o
        teto, aborta o que resta (marca job e etapas ainda 'pending' como
        'failed', com motivo explícito) e retorna False. Sem isso, um
        retry mal comportado poderia gerar uma fatura desagradável.
        """
        steps = await self.get_steps(job_id)
        total_cost = sum(s["cost_cents"] for s in steps)
        if total_cost < max_cost_cents:
            return True

        reason = f"Custo do job (US$ {total_cost / 100:.2f}) excedeu o teto configurado (US$ {max_cost_cents / 100:.2f})."
        async with self.conn.transaction():
            await self.conn.execute(
                "UPDATE deck_jobs SET status = 'failed', cost_cents = $2, "
                "failure_reason = $3, updated_at = now() WHERE id = $1",
                job_id,
                total_cost,
                reason,
            )
            await self.conn.execute(
                "UPDATE deck_job_steps SET status = 'failed', error = $2, updated_at = now() "
                "WHERE job_id = $1 AND status = 'pending'",
                job_id,
                reason,
            )
        return False
