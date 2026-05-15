from pydantic_core import core_schema
from app.models.report import NotebookDefaultResponse
from fastapi import APIRouter, HTTPException
from app.models.report import (
    ReportRequest,
    NotebookRequest,
    ReportResponse,
    NotebookDefaultResponse,
)
from app.services.report_service import (
    create_report,
    create_report_mock,
    create_slides_from_notebook,
)
from app.core.settings import settings
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
        extra={
            "title": req.notebook_title,
            "message_count": len(req.messages),
            "use_mock": settings.USE_MOCK_REPORT,
        },
    )

    try:
        if settings.USE_MOCK_REPORT:
            response = await create_report_mock(req)
        else:
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


@router.post("/create-slides", response_model=NotebookDefaultResponse)
async def create_slides_endpoint(req: NotebookRequest):
    try:
        response = await create_slides_from_notebook(req)
        logger.info(
            "Slides gerados com sucesso", extra={"notebook_id": response.notebook_id}
        )
        return response
    except Exception as e:
        logger.exception("Erro ao gerar slides no NotebookLM")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar slides: {str(e)}")
