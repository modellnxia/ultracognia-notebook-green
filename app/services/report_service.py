"""
Serviço de geração de relatórios via NotebookLM.

Expõe os seguintes fluxos públicos:

  1. orchestrate_prepare_notebook() — orquestra toda a preparação: checa cache
     no banco, valida usuário, busca mensagens, cria o notebook no NotebookLM
     e persiste o resultado. Deve ser chamado pelo router e pelo job de backup.

  2. create_report() — recebe apenas o notebook_id e gera o artefato de
     relatório via artifacts API. Não cria nem altera o notebook.

  3. create_slides_from_notebook() — gera o slide deck para um notebook preparado.

Funções privadas (prefixo _) não devem ser chamadas diretamente de fora deste módulo.
"""

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from notebooklm import NotebookLMClient, SlideDeckFormat, SlideDeckLength
from notebooklm.rpc import ReportFormat

from app.core.settings import settings
from app.models.report import (
    NotebookDefaultResponse,
    NotebookRequest,
    PrepareNotebookResponse,
    ReportRequest,
    ReportResponse,
)
from app.repositories.conversations import ConversationMessageRepository
from app.repositories.notebooks import NotebookRepository
from app.services.context_managers import with_secret_prompt

load_dotenv()
logger = logging.getLogger(__name__)

# ── Configuração via .env ────────────────────────────────────────────────────

_OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "./outputs"))

# Prompt customizado para o formato CUSTOM do generate_report
_CUSTOM_REPORT_PROMPT = (
    "Com base no histórico de conversa fornecido, gere um relatório completo com: "
    "1) Resumo executivo, "
    "2) Principais tópicos e descobertas, "
    "3) Análise crítica, "
    "4) Conclusões e recomendações."
)

# Separador entre mensagens — facilita leitura pelo NotebookLM
_MESSAGE_SEPARATOR = "\n\n---\n\n"


# ── Helpers privados ─────────────────────────────────────────────────────────


def _ensure_output_dir() -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR


def _timestamped_title(title: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{title}_{ts}"


def _join_messages(messages: list[str]) -> str:
    """Une a lista de mensagens em um único texto coerente."""
    return _MESSAGE_SEPARATOR.join(msg.strip() for msg in messages if msg.strip())


# ── Integrações privadas com NotebookLM ─────────────────────────────────────


async def _call_notebooklm_prepare(
    user_name: str, target_date: date, messages: list[str]
) -> PrepareNotebookResponse:
    """
    Integração direta com a API do NotebookLM. Não acessa banco de dados.

      1. Constrói o título no formato Nome_Usuario-Data.
      2. Cria o notebook no NotebookLM.
      3. Adiciona as mensagens como fonte principal.
      4. Retorna notebook_id e notebook_title.
    """
    unified_text = _join_messages(messages)
    formatted_name = user_name.replace(" ", "_")
    titled = f"{formatted_name}-{target_date}"

    async with await NotebookLMClient.from_storage() as client:
        # 1. Cria o notebook
        logger.info("Criando notebook: '%s'", titled)
        nb = await client.notebooks.create(titled)
        nb_id = nb.id
        logger.debug("Notebook criado — ID: %s", nb_id)

        # 2. Adiciona conversa como fonte principal
        conv_source = await client.sources.add_text(
            nb_id,
            content=unified_text,
            title=f"Histórico de Conversa - {target_date.strftime('%d/%m/%Y')}",
            wait=True,
        )
        logger.debug(
            "Fonte de conversa adicionada — source_id: %s (%d chars)",
            conv_source.id,
            len(unified_text),
        )

    logger.info("Notebook preparado — ID: %s, título: %s", nb_id, titled)
    return PrepareNotebookResponse(
        notebook_id=nb_id,
        notebook_title=titled,
        from_cache=False,
    )


# ── Serviços públicos ────────────────────────────────────────────────────────


async def orchestrate_prepare_notebook(
    conn: asyncpg.Connection,
    user_id: UUID,
    target_date: date,
    force_recreate: bool = False,
) -> PrepareNotebookResponse:
    """
    Orquestra a preparação completa de um notebook para um usuário e data.

    Centraliza a lógica compartilhada entre o endpoint HTTP e o job de backup:

      1. Checa se já existe um notebook em cache no banco (skip se houver).
      2. Busca o nome do usuário na tabela users.
      3. Busca as mensagens do dia na tabela conversations.
      4. Chama _call_notebooklm_prepare() para criar o notebook na API externa.
      5. Persiste o notebook_id no banco via NotebookRepository.
      6. Retorna PrepareNotebookResponse.

    Exceções:
        ValueError: se usuário não encontrado ou sem mensagens na data.
        Qualquer exceção da API do NotebookLM é propagada.
    """
    nb_repo = NotebookRepository(conn)

    # 1. Checa cache no banco
    if not force_recreate:
        cached = await nb_repo.get_notebook_by_user_and_date(user_id, target_date)
        if cached:
            logger.info(
                "Notebook encontrado no cache — notebook_id: %s",
                cached["notebook_id"],
            )
            return PrepareNotebookResponse(
                notebook_id=cached["notebook_id"],
                notebook_title=cached["notebook_title"],
                from_cache=True,
            )
    else:
        logger.info("Recriação forçada solicitada (bypassing cache)")

    # 2. Busca nome do usuário
    user_row = await conn.fetchrow("SELECT name FROM users WHERE id = $1", user_id)
    if not user_row:
        raise ValueError(f"Usuário não encontrado: {user_id}")
    user_name = user_row["name"]

    # 3. Busca mensagens do dia
    conv_repo = ConversationMessageRepository(conn)
    rows = await conv_repo.fetch_messages_by_user_and_date(user_id, target_date)
    if not rows:
        raise ValueError(
            f"Nenhuma mensagem encontrada para user_id={user_id} na data {target_date}"
        )
    messages = [f"[{row['role'].upper()}] {row['content']}" for row in rows]
    logger.info("%d mensagem(ns) encontrada(s) para user_id=%s.", len(messages), user_id)

    # 4. Cria notebook no NotebookLM
    response = await _call_notebooklm_prepare(user_name, target_date, messages)

    # 5. Persiste no banco
    await nb_repo.save_notebook_id(
        user_id=user_id,
        notebook_id=response.notebook_id,
        notebook_title=response.notebook_title,
        target_date=target_date,
    )
    logger.info(
        "Notebook salvo no banco — notebook_id: %s", response.notebook_id
    )

    return response


async def create_report(conn: asyncpg.Connection, req: ReportRequest) -> ReportResponse:
    """
    Gera o artefato de relatório para um notebook já preparado.

      1. Re-injeta o prompt proprietário via ``with_secret_prompt``.
      2. Gera o relatório via artifacts.generate_report() (CUSTOM).
      3. Aguarda a conclusão com wait_for_completion().
      4. Baixa o conteúdo do relatório.
      5. Persiste o conteúdo no banco de dados e retorna.

    O notebook deve ter sido previamente criado via prepare_notebook().
    """
    nb_id = req.notebook_id

    async with await NotebookLMClient.from_storage() as client:

        # 1. Re-injeta o prompt proprietário e gera o relatório
        async with with_secret_prompt(client, nb_id):
            logger.info(
                "Iniciando geração de relatório via artifacts API — notebook: %s", nb_id
            )
            gen_status = await client.artifacts.generate_report(
                nb_id,
                report_format=ReportFormat.CUSTOM,
                language="pt",
                custom_prompt=_CUSTOM_REPORT_PROMPT,
            )
            logger.debug("Geração iniciada — task_id: %s", gen_status.task_id)

            # 2. Aguarda conclusão
            logger.info("Aguardando conclusão do relatório (timeout: 300s)...")
            final_status = await client.artifacts.wait_for_completion(
                nb_id,
                gen_status.task_id,
                timeout=300.0,
            )

            if final_status.is_failed:
                raise RuntimeError(
                    f"Geração de relatório falhou — task_id: {gen_status.task_id}"
                )

            logger.info("Relatório concluído — artifact_id: %s", gen_status.task_id)

        # 3. Baixa o conteúdo do relatório direto para o arquivo final
        report_path = _ensure_output_dir() / f"{nb_id}_relatorio.md"
        await client.artifacts.download_report(
            nb_id,
            output_path=str(report_path),
            artifact_id=gen_status.task_id,
        )
        report_content = report_path.read_text(encoding="utf-8")
        logger.debug("Relatório baixado salvo direto em disco (%d chars).", len(report_content))

    # 4. Persiste no banco de dados
    repo = NotebookRepository(conn)
    await repo.update_notebook_report_by_id(
        notebook_id=nb_id,
        report_content=report_content,
        report_path=str(report_path),
    )
    logger.info("Relatório atualizado no banco de dados.")

    return ReportResponse(
        notebook_id=nb_id,
        notebook_title=nb_id,  # título não disponível sem nova consulta ao LM
        report=report_content,
        report_path=str(report_path),
    )


async def create_slides_from_notebook(req: NotebookRequest) -> NotebookDefaultResponse:
    """
    Cria slides a partir do relatório gerado pelo NotebookLM.
    Re-injeta o prompt proprietário via ``with_secret_prompt`` antes da geração
    e o remove ao final, mesmo em caso de falha.
    """
    nb_id = req.notebook_id

    async with await NotebookLMClient.from_storage() as client:
        # 1. Re-injeta o prompt proprietário e gera o slide deck
        async with with_secret_prompt(client, nb_id):
            logger.info("Gerando slide deck — notebook: %s", nb_id)
            slide_status = await client.artifacts.generate_slide_deck(
                nb_id,
                slide_format=SlideDeckFormat.PRESENTER_SLIDES,
                slide_length=SlideDeckLength.DEFAULT,
                instructions=settings.SLIDE_DECK_INSTRUCTION,
            )
            await client.artifacts.wait_for_completion(
                nb_id, slide_status.task_id, timeout=1200
            )

        # 2. Baixa o slide deck
        output_dir = _ensure_output_dir()
        slides_path = output_dir / f"{nb_id}_slides.pdf"
        await client.artifacts.download_slide_deck(nb_id, str(slides_path))
        logger.info("Slides salvos em: %s", slides_path)

        return NotebookDefaultResponse(
            notebook_id=nb_id,
            message="Slides criados com sucesso",
            status=True,
        )
