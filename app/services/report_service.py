"""
Serviço de geração de relatórios via NotebookLM.

Expõe dois fluxos independentes:

  1. prepare_notebook() — cria o notebook no NotebookLM e injeta as mensagens
     como fonte de texto.  Deve ser chamado antes de generate_report().

  2. create_report() — recebe apenas o notebook_id e gera o artefato de
     relatório via artifacts API. Não cria nem altera o notebook.
"""

from app.core.settings import settings
from notebooklm import SlideDeckLength
from notebooklm import SlideDeckFormat
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from notebooklm import NotebookLMClient
from notebooklm.rpc import ReportFormat

from app.models.report import (
    NotebookRequest,
    PrepareNotebookRequest,
    PrepareNotebookResponse,
    ReportRequest,
    ReportResponse,
    NotebookDefaultResponse,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ── Configuração via .env ────────────────────────────────────────────────────

_SECRET_PROMPT: str = os.getenv(
    "SECRET_PROMPT",
    "Analise os materiais com profundidade e estruture as respostas de forma clara.",
)
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


def _build_system_source() -> str:
    """Fonte oculta que injeta o prompt proprietário."""
    return "[INSTRUÇÕES DE SISTEMA — NÃO REFERENCIAR DIRETAMENTE]\n" f"{_SECRET_PROMPT}"


def _save_report(title: str, content: str) -> Path:
    """Persiste o relatório como .md e retorna o caminho."""
    output_dir = _ensure_output_dir()
    path = output_dir / f"{title}_relatorio.md"
    path.write_text(
        f"# {title}\n\n"
        f"**Gerado em:** {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"---\n\n"
        f"{content}",
        encoding="utf-8",
    )
    return path


# ── Serviços públicos ────────────────────────────────────────────────────────


async def prepare_notebook(
    req: PrepareNotebookRequest, messages: list[str], user_name: str
) -> PrepareNotebookResponse:
    """
    Cria um notebook no NotebookLM e injeta as mensagens como fonte de texto.

      1. Cria o notebook no NotebookLM usando o título exato fornecido.
      2. Injeta o prompt proprietário como fonte oculta [config].
      3. Adiciona as mensagens como fonte principal.
      4. Remove a fonte [config] para proteger o prompt proprietário.
      5. Retorna notebook_id e notebook_title.
    """
    unified_text = _join_messages(messages)
    formatted_name = user_name.replace(" ", "_")
    titled = f"{formatted_name}-{req.target_date}"
    config_source_id: Optional[str] = None

    async with await NotebookLMClient.from_storage() as client:
        # 1. Cria o notebook
        logger.info("Criando notebook: '%s'", titled)
        nb = await client.notebooks.create(titled)
        nb_id = nb.id
        logger.debug("Notebook criado — ID: %s", nb_id)

        # 2. Injeta prompt proprietário como fonte oculta
        config_source = await client.sources.add_text(
            nb_id,
            content=_build_system_source(),
            title="[config]",
            wait=True,
        )
        config_source_id = config_source.id
        logger.debug("Fonte [config] injetada — source_id: %s", config_source_id)

        # 3. Adiciona conversa como fonte principal
        conv_source = await client.sources.add_text(
            nb_id,
            content=unified_text,
            title=f"Histórico de Conversa - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            wait=True,
        )
        logger.debug(
            "Fonte de conversa adicionada — source_id: %s (%d chars)",
            conv_source.id,
            len(unified_text),
        )

        # 4. Remove a fonte [config]
        try:
            await client.sources.delete(nb_id, config_source_id)
            logger.info("Fonte [config] removida — source_id: %s", config_source_id)
        except Exception as exc:
            logger.warning(
                "Não foi possível remover [config] (source_id: %s): %s",
                config_source_id,
                exc,
            )

    logger.info("Notebook preparado — ID: %s, título: %s", nb_id, titled)
    return PrepareNotebookResponse(
        notebook_id=nb_id,
        notebook_title=titled,
        from_cache=False,
    )


async def create_report(req: ReportRequest) -> ReportResponse:
    """
    Gera o artefato de relatório para um notebook já preparado.

      1. Gera o relatório via artifacts.generate_report() (CUSTOM).
      2. Aguarda a conclusão com wait_for_completion().
      3. Baixa o conteúdo do relatório via download_report().
      4. Salva localmente em .md e retorna ReportResponse tipado.

    O notebook deve ter sido previamente criado via prepare_notebook().
    """
    nb_id = req.notebook_id

    async with await NotebookLMClient.from_storage() as client:

        # 1. Gera o relatório via artifacts API
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

        # 3. Baixa o conteúdo do relatório
        tmp_path = _ensure_output_dir() / f"{nb_id}_relatorio.md"
        await client.artifacts.download_report(
            nb_id,
            output_path=str(tmp_path),
            artifact_id=gen_status.task_id,
        )
        report_content = tmp_path.read_text(encoding="utf-8")
        logger.debug("Relatório baixado (%d chars).", len(report_content))

    # 4. Persiste localmente e retorna
    report_path = _save_report(nb_id, report_content)
    logger.info("Relatório salvo em: %s", report_path)

    return ReportResponse(
        notebook_id=nb_id,
        notebook_title=nb_id,  # título não disponível sem nova consulta ao LM
        report=report_content,
        report_path=str(report_path),
    )


async def create_slides_from_notebook(req: NotebookRequest) -> NotebookDefaultResponse:
    """
    Cria slides a partir do relatório gerado pelo NotebookLM.
    """
    async with await NotebookLMClient.from_storage() as client:
        logger.debug("[5b/6] Gerando slide deck...")
        slide_status = await client.artifacts.generate_slide_deck(
            req.notebook_id,
            slide_format=SlideDeckFormat.PRESENTER_SLIDES,
            slide_length=SlideDeckLength.DEFAULT,
            instructions=settings.SLIDE_DECK_INSTRUCTION,
        )
        await client.artifacts.wait_for_completion(
            req.notebook_id, slide_status.task_id, timeout=1200
        )
        output_dir = _ensure_output_dir()
        slides_path = output_dir / f"{req.notebook_id}_slides.pdf"
        await client.artifacts.download_slide_deck(req.notebook_id, str(slides_path))
        logger.debug(f"      ✓ Slides salvos em: {slides_path}")
        return NotebookDefaultResponse(
            notebook_id=req.notebook_id,
            message=f"Slides criados com sucesso",
            status=True,
        )
