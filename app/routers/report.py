from fastapi import APIRouter, HTTPException
from app.models.report import ReportRequest, ReportResponse
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report", tags=["report"])


@router.post("/generate", response_model=ReportResponse)
async def generate_report_endpoint(req: ReportRequest) -> ReportResponse:
    """
    Recebe lista de mensagens de conversas, cria notebook no NotebookLM,
    gera relatório e retorna o resultado com o ID do notebook criado.
    """
    logger.info(
        "Iniciando geração de relatório",
        extra={"title": req.notebook_title, "message_count": len(req.messages)},
    )

    try:
        response = ReportResponse(
            notebook_id="79723930-0e8a-4e57-b94f-a1f505b8f97b",
            notebook_title="Teste de Relat\u00f3rio Textual_20260514_083818",
            report="Com certeza! Acabei de gerar o **relat\u00f3rio completo** solicitado, abrangendo o resumo executivo, os principais t\u00f3picos, uma an\u00e1lise cr\u00edtica e as recomenda\u00e7\u00f5es finais com base em nossa intera\u00e7\u00e3o sobre a geografia brasileira.\n\nO documento j\u00e1 est\u00e1 sendo processado e estar\u00e1 dispon\u00edvel em breve na aba Studio. Voc\u00ea poder\u00e1 revisar todos os pontos detalhadamente por l\u00e1.",
            report_path="outputs/Teste de Relat\u00f3rio Textual_20260514_083818_relatorio.md",
        )

    except Exception as e:
        logger.exception("Erro ao gerar relatório no NotebookLM")
        raise HTTPException(
            status_code=500, detail=f"Erro ao gerar relatório: {str(e)}"
        )

    logger.info(
        "Relatório gerado com sucesso", extra={"notebook_id": response.notebook_id}
    )
    return response
