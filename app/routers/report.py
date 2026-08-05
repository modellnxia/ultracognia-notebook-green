from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, Security, status
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse
from app.core.settings import settings
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
    get_generation_status,
)
from app.core.database import get_db_conn
import logging

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key(api_key: str = Security(api_key_header)):
    if api_key == settings.API_KEY:
        return api_key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API Key inválida ou ausente",
    )

router = APIRouter(
    prefix="/report", 
    tags=["report"]
)


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


@router.get("/download-slides/{notebook_id}")
async def download_slides_endpoint(notebook_id: str):
    """
    Baixa o PDF do slide deck gerado por POST /report/create-slides.

    O arquivo é salvo em OUTPUT_DIR/{notebook_id}_slides.pdf no momento da
    criação (ver create_slides_from_notebook em report_service.py) — esse
    endpoint só serve o arquivo já existente, não gera nada novo.
    """
    slides_path = Path(settings.OUTPUT_DIR) / f"{notebook_id}_slides.pdf"
    if not slides_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Slides não encontrados para notebook_id={notebook_id}. "
            "Gere via POST /report/create-slides primeiro.",
        )
    return FileResponse(
        path=slides_path,
        media_type="application/pdf",
        filename=f"{notebook_id}_slides.pdf",
    )


@router.get("/generation-status/{notebook_id}")
async def generation_status_endpoint(notebook_id: str):
    """
    Status mais recente (em memória, por processo) da geração de relatório/
    slides em andamento para esse notebook_id — usado pela barra de progresso
    da interface de teste. Estados vêm direto do NotebookLM: pending,
    in_progress, completed, failed, not_found, removed. "unknown" quando
    nada foi rastreado ainda (geração não iniciada, ou API reiniciou).
    """
    return get_generation_status(notebook_id) or {"status": "unknown"}


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
        extra={"user_id": str(req.user_id), "date": str(req.start_date)},
    )

    try:
        async for conn in get_db_conn():
            return await orchestrate_prepare_notebook(
                conn=conn,
                user_id=req.user_id,
                start_date=req.start_date,
                end_date=req.end_date,
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
