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
    prepare_notebook,
)
from app.core.database import get_db_conn
from app.repositories.conversations import ConversationMessageRepository
from app.repositories.notebooks import NotebookRepository
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
    logger.info("Iniciando geração de relatório", extra={"notebook_id": req.notebook_id})

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
async def prepare_notebook_endpoint(req: PrepareNotebookRequest) -> PrepareNotebookResponse:
    """
    Prepara um notebook no NotebookLM a partir das mensagens do banco de dados.

    Fluxo:
      1. Checa o banco: se já existe um notebook para user_id + target_date, retorna
         o ID existente com from_cache=True (sem chamar o NotebookLM).
      2. Se não existir (ou force_recreate=True): busca as mensagens do banco,
         cria um novo notebook no NotebookLM, injeta as mensagens como fonte,
         persiste o notebook_id no banco e retorna from_cache=False.
    """
    logger.info(
        "Preparando notebook",
        extra={"user_id": str(req.user_id), "date": str(req.target_date)},
    )

    # ── 1. Checa cache no banco ───────────────────────────────────────────────
    try:
        async for conn in get_db_conn():
            nb_repo = NotebookRepository(conn)

            if not req.force_recreate:
                cached = await nb_repo.get_notebook_by_user_and_date(
                    req.user_id, req.target_date
                )
                if cached:
                    logger.info(
                        "Notebook encontrado no cache",
                        extra={"notebook_id": cached["notebook_id"]},
                    )
                    return PrepareNotebookResponse(
                        notebook_id=cached["notebook_id"],
                        notebook_title=cached["notebook_title"],
                        from_cache=True,
                    )
            else:
                logger.info("Recriação forçada solicitada (bypassing cache)")

            # ── 2. Busca mensagens no banco ───────────────────────────────────
            conv_repo = ConversationMessageRepository(conn)
            rows = await conv_repo.fetch_messages_by_user_and_date(
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

    # ── 3. Monta lista de mensagens formatadas ────────────────────────────────
    messages = [f"[{row['role'].upper()}] {row['content']}" for row in rows]

    # ── 4. Cria notebook no NotebookLM e injeta mensagens ────────────────────
    try:
        response = await prepare_notebook(req, messages)
    except Exception as e:
        logger.exception("Erro ao preparar notebook no NotebookLM")
        raise HTTPException(
            status_code=500, detail=f"Erro ao preparar notebook: {str(e)}"
        )

    # ── 5. Persiste notebook_id no banco ─────────────────────────────────────
    try:
        async for conn in get_db_conn():
            nb_repo = NotebookRepository(conn)
            await nb_repo.save_notebook_id(
                user_id=req.user_id,
                notebook_id=response.notebook_id,
                notebook_title=response.notebook_title,
                target_date=req.target_date,
            )
    except Exception as e:
        logger.exception("Erro ao persistir notebook_id no banco")
        raise HTTPException(
            status_code=500, detail=f"Erro ao salvar notebook no banco: {str(e)}"
        )

    logger.info(
        "Notebook preparado e salvo no banco",
        extra={"notebook_id": response.notebook_id},
    )
    return response
