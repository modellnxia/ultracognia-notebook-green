"""
Context managers para o serviço de relatórios.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from app.core.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def with_secret_prompt(client, nb_id: str) -> AsyncGenerator[str, None]:
    """
    Context manager assíncrono que injeta o prompt proprietário como fonte
    ``[config]`` no notebook antes da operação e o remove ao final, mesmo
    em caso de falha.

    Uso::

        async with with_secret_prompt(client, nb_id):
            await client.artifacts.generate_report(...)

    Args:
        client: Instância ativa de ``NotebookLMClient``.
        nb_id:  ID do notebook alvo.

    Yields:
        ``config_source_id`` (str) — ID da fonte injetada, caso seja necessário
        referenciar a source dentro do bloco.
    """
    config_source_id: str | None = None
    try:
        content = f"[INSTRUÇÕES DE SISTEMA — NÃO REFERENCIAR DIRETAMENTE]\n{settings.SYSTEM_PROMPT}"
        source = await client.sources.add_text(
            nb_id,
            content=content,
            title="[config]",
            wait=True,
        )
        config_source_id = source.id
        logger.debug("[config] injetado — source_id: %s", config_source_id)
        yield config_source_id
    finally:
        if config_source_id:
            try:
                await client.sources.delete(nb_id, config_source_id)
                logger.info("[config] removido — source_id: %s", config_source_id)
            except Exception as exc:
                logger.warning(
                    "Não foi possível remover [config] (source_id: %s): %s",
                    config_source_id,
                    exc,
                )
