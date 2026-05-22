"""
Configuração do APScheduler para o backup diário de notebooks.

O scheduler é iniciado/encerrado no lifespan da FastAPI (main.py).
Não requer broker externo — roda dentro do processo da API usando
AsyncIOScheduler (compatível com o event loop do FastAPI/uvicorn).
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.settings import settings
from app.scheduler.backup_job import backup_notebooks_daily


def create_scheduler() -> AsyncIOScheduler:
    """
    Cria e configura o scheduler com o job de backup diário.

    O job é registrado com `replace_existing=True` para que reinícios
    da aplicação não criem entradas duplicadas no scheduler.

    Horário configurável via variáveis de ambiente:
      BACKUP_SCHEDULE_HOUR   (padrão: 23)
      BACKUP_SCHEDULE_MINUTE (padrão: 0)
    """
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

    scheduler.add_job(
        backup_notebooks_daily,
        trigger="cron",
        hour=settings.BACKUP_SCHEDULE_HOUR,
        minute=settings.BACKUP_SCHEDULE_MINUTE,
        id="backup_notebooks_daily",
        replace_existing=True,
    )

    return scheduler
