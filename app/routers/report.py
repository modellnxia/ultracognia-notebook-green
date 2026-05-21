from fastapi import APIRouter, HTTPException
from app.models.report import (
    ReportRequest,
    NotebookRequest,
    ReportResponse,
    NotebookDefaultResponse,
    UserDateReportRequest,
)
from app.services.report_service import (
    create_report,
    create_report_mock,
    create_slides_from_notebook,
)
from app.core.database import get_db_conn
from app.repositories.conversations import ConversationMessageRepository
from app.repositories.notebooks import NotebookRepository
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


@router.post("/generate-from-db", response_model=ReportResponse)
async def generate_report_from_db(req: UserDateReportRequest) -> ReportResponse:
    """
    Busca todas as mensagens de um usuário em uma data específica no banco
    de dados e gera um relatório via NotebookLM.
    """
    logger.info(
        "Buscando mensagens do banco",
        extra={"user_id": str(req.user_id), "date": str(req.target_date)},
    )

    # ── 1. Verifica cache e busca mensagens no banco ─────────────────────────
    try:
        async for conn in get_db_conn():
            # Checa se o relatório já existe para não recriar (se não forçado)
            if not req.force_recreate:
                nb_repo = NotebookRepository(conn)
                cached_notebook = await nb_repo.get_notebook_by_user_and_date(
                    req.user_id, req.target_date
                )
                
                if cached_notebook:
                    logger.info(
                        "Relatório encontrado no cache", 
                        extra={"notebook_id": cached_notebook["notebook_id"]}
                    )
                    return ReportResponse(
                        notebook_id=cached_notebook["notebook_id"],
                        notebook_title=cached_notebook["notebook_title"],
                        report=cached_notebook["report_content"],
                        report_path=cached_notebook["report_path"],
                    )
            else:
                logger.info("Recriação forçada solicitada (bypassing cache)")

            # Se não existir ou for force_recreate, busca as mensagens
            repo = ConversationMessageRepository(conn)
            rows = await repo.fetch_messages_by_user_and_date(
                req.user_id, req.target_date
            )

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao consultar banco de dados")
        raise HTTPException(
            status_code=500, detail=f"Erro ao consultar banco: {str(e)}"
        )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhuma mensagem encontrada para user_id={req.user_id} na data {req.target_date}",
        )

    logger.info("%d mensagem(ns) encontrada(s).", len(rows))

    # ── 2. Formata mensagens como strings ────────────────────────────────────
    messages = [f"[{row['role'].upper()}] {row['content']}" for row in rows]

    # ── 3. Monta ReportRequest e gera relatório ─────────────────────────────
    report_req = ReportRequest(
        messages=messages,
        notebook_title=req.notebook_title,
        notebook_id=req.notebook_id,
    )

    try:
        if settings.USE_MOCK_REPORT:
            response = await create_report_mock(report_req)
        else:
            response = await create_report(report_req)
            
        # ── 4. Salva no cache do banco de dados ─────────────────────────────────
        async for conn in get_db_conn():
            nb_repo = NotebookRepository(conn)
            await nb_repo.save_notebook(
                user_id=req.user_id,
                notebook_id=response.notebook_id,
                notebook_title=response.notebook_title,
                target_date=req.target_date,
                report_content=response.report,
                report_path=response.report_path,
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
