from fastapi import APIRouter, HTTPException
from app.models.report import (
    ReportRequest,
    NotebookRequest,
    ReportResponse,
    NotebookDefaultResponse,
    PrepareNotebookRequest,
    PrepareNotebookResponse,
)
from app.services.report_service import (
    create_report,
    create_slides_from_notebook,
    orchestrate_prepare_notebook,
)
from app.core.database import get_db_conn
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report", tags=["report"])


@router.post("/generate", response_model=ReportResponse)
async def generate_report_endpoint(req: ReportRequest) -> ReportResponse:
    """
    Gera um artefato de relatório para um notebook já preparado no NotebookLM.

    O notebook deve ter sido criado previamente via POST /report/prepare-notebook.
    Recebe apenas o notebook_id e aciona a geração do relatório via artifacts API.
    """
    logger.info(
        "Iniciando geração de relatório", extra={"notebook_id": req.notebook_id}
    )

    try:
        async for conn in get_db_conn():
            response = await create_report(conn, req)
            logger.info(
                "Relatório gerado com sucesso", extra={"notebook_id": response.notebook_id}
            )
            return response
    except Exception as e:
        logger.exception("Erro ao gerar relatório no NotebookLM")
        raise HTTPException(
            status_code=500, detail=f"Erro ao gerar relatório: {str(e)}"
        )


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


@router.post("/prepare-notebook", response_model=PrepareNotebookResponse)
async def prepare_notebook_endpoint(
    req: PrepareNotebookRequest,
) -> PrepareNotebookResponse:
    """
    Prepara um notebook no NotebookLM a partir das mensagens do banco de dados.

    Delega toda a orquestração para orchestrate_prepare_notebook():
      - Checa cache, valida usuário, busca mensagens, cria notebook, salva no banco.
    """
    logger.info(
        "Preparando notebook",
        extra={"user_id": str(req.user_id), "date": str(req.target_date)},
    )

    try:
        async for conn in get_db_conn():
            return await orchestrate_prepare_notebook(
                conn=conn,
                user_id=req.user_id,
                target_date=req.target_date,
                force_recreate=req.force_recreate,
            )
    except ValueError as e:
        msg = str(e)
        if "não encontrado" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=404, detail=msg)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao preparar notebook")
        raise HTTPException(status_code=500, detail=f"Erro ao preparar notebook: {str(e)}")
