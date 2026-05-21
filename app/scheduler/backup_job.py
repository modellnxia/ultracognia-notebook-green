"""
Job de backup diário de notebooks.

Para cada usuário que teve mensagens no dia corrente, cria um notebook
no NotebookLM ingerindo o histórico de conversas — sem gerar artefatos.

O job é disparado pelo APScheduler (ver app/scheduler/scheduler.py) e
abre sua própria conexão ao banco independente do pool do FastAPI.
"""

import asyncpg
import logging
from datetime import date
from uuid import UUID

from app.core.settings import settings
from app.models.report import PrepareNotebookRequest
from app.repositories.conversations import ConversationMessageRepository
from app.repositories.notebooks import NotebookRepository
from app.repositories.users import UserRepository
from app.services.report_service import prepare_notebook

logger = logging.getLogger(__name__)


async def backup_notebooks_daily() -> None:
    """
    Job agendado: cria (ou reutiliza) um notebook de backup para cada
    usuário com mensagens no dia atual.

    Fluxo por usuário:
      1. Checa se já existe notebook no banco (cache hit → skip).
      2. Busca as mensagens do dia.
      3. Cria o notebook no NotebookLM via prepare_notebook().
      4. Persiste o notebook_id no banco.

    Falhas individuais são capturadas e logadas sem interromper os demais.
    """
    today = date.today()
    logger.info("Iniciando backup diário de notebooks — data: %s", today)

    conn = await asyncpg.connect(dsn=settings.DATABASE_URL)
    try:
        user_ids = await UserRepository(conn).fetch_users_with_messages_on_date(today)
        logger.info("%d usuário(s) com mensagens hoje.", len(user_ids))

        created = 0
        skipped = 0

        for user_id in user_ids:
            try:
                nb_repo = NotebookRepository(conn)

                # ── 1. Checa cache ────────────────────────────────────────
                cached = await nb_repo.get_notebook_by_user_and_date(user_id, today)
                if cached:
                    logger.info(
                        "Notebook já existe para user_id=%s — pulando.", user_id
                    )
                    skipped += 1
                    continue

                # ── 2. Busca mensagens ────────────────────────────────────
                rows = await ConversationMessageRepository(
                    conn
                ).fetch_messages_by_user_and_date(user_id, today)

                if not rows:
                    logger.warning(
                        "Nenhuma mensagem encontrada para user_id=%s — pulando.", user_id
                    )
                    skipped += 1
                    continue

                messages = [f"[{row['role'].upper()}] {row['content']}" for row in rows]

                # ── 3. Cria notebook no NotebookLM ────────────────────────
                req = PrepareNotebookRequest(
                    user_id=user_id,
                    target_date=today,
                    notebook_title="Backup Diário",
                )
                response = await prepare_notebook(req, messages)

                # ── 4. Persiste notebook_id no banco ──────────────────────
                await nb_repo.save_notebook_id(
                    user_id=user_id,
                    notebook_id=response.notebook_id,
                    notebook_title=response.notebook_title,
                    target_date=today,
                )

                logger.info(
                    "Notebook criado — user_id=%s, notebook_id=%s",
                    user_id,
                    response.notebook_id,
                )
                created += 1

            except Exception:
                logger.exception(
                    "Erro ao criar notebook para user_id=%s — continuando.", user_id
                )

        logger.info(
            "Backup concluído — criados: %d, pulados: %d", created, skipped
        )

    finally:
        await conn.close()
