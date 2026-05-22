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

from app.core.settings import settings
from app.repositories.users import UserRepository
from app.services.report_service import orchestrate_prepare_notebook

logger = logging.getLogger(__name__)


async def backup_notebooks_daily() -> None:
    """
    Job agendado: cria (ou reutiliza) um notebook de backup para cada
    usuário com mensagens no dia atual.

    Fluxo por usuário:
      1. Delega toda a orquestração para orchestrate_prepare_notebook().
         (cache check, busca de mensagens, criação no NotebookLM, persistência)

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
                response = await orchestrate_prepare_notebook(
                    conn=conn,
                    user_id=user_id,
                    start_date=today,
                )

                if response.from_cache:
                    logger.info(
                        "Notebook já existe para user_id=%s — pulando.", user_id
                    )
                    skipped += 1
                else:
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
