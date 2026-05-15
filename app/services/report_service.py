"""
Serviço de geração de relatórios via NotebookLM.

Orquestra o fluxo completo: prepara o notebook, gera o relatório
estruturado via artifacts API, garante a remoção da fonte proprietária
[config] ao final e persiste o resultado localmente.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from notebooklm import NotebookLMClient
from notebooklm.rpc import ReportFormat

from app.models.report import ReportRequest, ReportResponse

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


# ── Serviço público ──────────────────────────────────────────────────────────


async def create_report(req: ReportRequest) -> ReportResponse:
    """
    Orquestra o fluxo completo de geração de relatório:

      1. Une as mensagens da conversa em texto único.
      2. Se notebook_id não fornecido: cria notebook e injeta o prompt
         proprietário como fonte oculta [config].
      3. Adiciona o histórico da conversa como fonte principal.
      4. Gera o relatório via artifacts.generate_report() (CUSTOM).
      5. Aguarda a conclusão com wait_for_completion().
      6. Baixa o conteúdo do relatório via download_report().
      7. Remove a fonte [config] para proteger o prompt proprietário.
      8. Salva localmente em .md e retorna ReportResponse tipado.
    """
    unified_text = _join_messages(req.messages)
    titled = _timestamped_title(req.notebook_title)
    config_source_id: Optional[str] = None

    async with await NotebookLMClient.from_storage() as client:

        # ── 1. Notebook: criar ou reutilizar ──────────────────────────────
        if req.notebook_id:
            logger.info("Usando notebook existente: %s", req.notebook_id)
            nb_id = req.notebook_id
        else:
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

        # ── 3. Adiciona conversa como fonte principal ─────────────────────
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

        # ── 4. Gera o relatório via artifacts API ─────────────────────────
        logger.info("Iniciando geração de relatório via artifacts API...")
        gen_status = await client.artifacts.generate_report(
            nb_id,
            report_format=ReportFormat.CUSTOM,
            language="pt",
            custom_prompt=_CUSTOM_REPORT_PROMPT,
        )
        logger.debug("Geração iniciada — task_id: %s", gen_status.task_id)

        # ── 5. Aguarda conclusão ──────────────────────────────────────────
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

        # ── 6. Baixa o conteúdo do relatório ─────────────────────────────
        tmp_path = _ensure_output_dir() / f"{titled}_relatorio.md"
        await client.artifacts.download_report(
            nb_id,
            output_path=str(tmp_path),
            artifact_id=gen_status.task_id,
        )
        report_content = tmp_path.read_text(encoding="utf-8")
        logger.debug("Relatório baixado (%d chars).", len(report_content))

        # ── 7. Remove a fonte [config] ────────────────────────────────────
        if config_source_id:
            try:
                await client.sources.delete(nb_id, config_source_id)
                logger.info("Fonte [config] removida — source_id: %s", config_source_id)
            except Exception as exc:
                logger.warning(
                    "Não foi possível remover [config] (source_id: %s): %s",
                    config_source_id,
                    exc,
                )

    # ── 8. Persiste localmente e retorna ─────────────────────────────────────
    report_path = _save_report(titled, report_content)
    logger.info("Relatório salvo em: %s", report_path)

    return ReportResponse(
        notebook_id=nb_id,
        notebook_title=titled,
        report=report_content,
        report_path=str(report_path),
    )


async def create_report_mock(req: ReportRequest) -> ReportResponse:
    """
    Retorna um mock do relatório para economizar quota da API durante desenvolvimento.
    """
    logger.info("Gerando relatório MOCK para economizar quota...")

    nb_id = req.notebook_id or "mock-notebook-id-123456789"
    titled = _timestamped_title(req.notebook_title or "Mock_Relatorio")

    mock_content = (
        "Este é um relatório gerado como MOCK para economizar a quota da API.\n\n"
        "## 1) Resumo executivo\n"
        "Resumo simulado com base nas mensagens recebidas.\n\n"
        "## 2) Principais tópicos e descobertas\n"
        "- Tópico simulado A\n"
        "- Tópico simulado B\n\n"
        "## 3) Análise crítica\n"
        "Análise crítica simulada. Todos os dados aqui são apenas placeholders para teste de interface e fluxo.\n\n"
        "## 4) Conclusões e recomendações\n"
        "Recomendações simuladas."
    )

    report_path = _save_report(titled, mock_content)
    logger.info("Relatório MOCK salvo em: %s", report_path)

    return ReportResponse(
        notebook_id=nb_id,
        notebook_title=titled,
        report=mock_content,
        report_path=str(report_path),
    )
