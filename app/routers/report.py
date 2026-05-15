from fastapi import APIRouter, HTTPException
from app.models.report import ReportRequest, ReportResponse
from app.services.report_service import create_report
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
        response = await create_report(req)

    except Exception as e:
        logger.exception("Erro ao gerar relatório no NotebookLM")
        raise HTTPException(
            status_code=500, detail=f"Erro ao gerar relatório: {str(e)}"
        )

    logger.info(
        "Relatório gerado com sucesso", extra={"notebook_id": response.notebook_id}
    )
    return response
