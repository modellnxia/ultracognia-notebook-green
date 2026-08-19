"""
Poller do pipeline de decks — processa a fila `deck_job_steps`, uma etapa por
vez, a cada intervalo curto. Roda no mesmo processo/scheduler da API (mesmo
`AsyncIOScheduler` que já dispara o backup semanal em
`app/scheduler/scheduler.py`) — sem fila externa (Redis/Celery), decisão
registrada em `app/decks/README.md`.

`register_deck_poller()` só adiciona um job no scheduler já existente — não
cria um scheduler próprio, pra não duplicar o que o host já tem.
"""

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.database import get_db_conn
from app.decks.repository import DeckJobRepository
from app.decks.steps import STEP_EXECUTORS

logger = logging.getLogger(__name__)

# Disjuntor de custo por job — teto configurável, US$ 5,00 por padrão.
_DEFAULT_MAX_COST_CENTS = 500


async def process_next_step() -> bool:
    """
    Processa UMA etapa pendente (a mais antiga, de qualquer job).

    Retorna True se processou algo, False se a fila estava vazia. Cada
    chamada é independente — não guarda estado nenhum em memória entre uma
    chamada e outra, por isso um processo novo (depois de reiniciar) retoma
    exatamente de onde o banco diz que parou.
    """
    async for conn in get_db_conn():
        repo = DeckJobRepository(conn)
        step = await repo.claim_next_pending_step()
        if step is None:
            return False

        executor = STEP_EXECUTORS[step["step"]]
        try:
            output_ref, input_tok, output_tok, cost = await executor(step["job_id"], conn)
            await repo.mark_step_completed(
                step["id"], output_ref, input_tok, output_tok, cost
            )
            logger.info(
                "Etapa '%s' concluída — job=%s, output_ref=%s",
                step["step"], step["job_id"], output_ref,
            )
        except Exception as exc:
            logger.exception("Etapa '%s' falhou — job=%s", step["step"], step["job_id"])
            await repo.mark_step_failed(step["id"], str(exc))

        max_cost_cents = int(os.getenv("DECK_JOB_MAX_COST_CENTS", _DEFAULT_MAX_COST_CENTS))
        within_budget = await repo.enforce_cost_cap(step["job_id"], max_cost_cents)
        if not within_budget:
            logger.warning("Job %s abortado — teto de custo excedido.", step["job_id"])
            return True

        await repo.finalize_job_if_all_steps_done(step["job_id"])
        return True
    return False


async def poll_once() -> None:
    """Chamado pelo scheduler a cada intervalo — processa uma etapa, se houver."""
    try:
        await process_next_step()
    except Exception:
        logger.exception("Erro inesperado no poller de decks.")


def register_deck_poller(scheduler: AsyncIOScheduler, interval_seconds: float = 5.0) -> None:
    scheduler.add_job(
        poll_once,
        trigger="interval",
        seconds=interval_seconds,
        id="deck_poller",
        replace_existing=True,
        max_instances=1,  # evita sobrepor execuções se uma etapa demorar mais que o intervalo
    )
    logger.info("Poller de decks registrado — intervalo: %.0fs.", interval_seconds)
