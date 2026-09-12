"""
Router novo e separado (2026-09-12, pedido explícito do usuário — não
misturar com app/routers/report.py) pro chat isolado com um notebook do
NotebookLM. Ver app/services/notebook_chat_service.py pra lógica completa.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.models.notebook_chat import NotebookChatRequest, NotebookChatResponse
from app.services.notebook_chat_service import (
    AmbiguousNotebookTitleError,
    NotebookNotFoundError,
    ask_notebook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notebook-chat", tags=["notebook-chat"])


@router.post("/ask", response_model=NotebookChatResponse)
async def ask_notebook_endpoint(req: NotebookChatRequest) -> NotebookChatResponse:
    """
    Pergunta isolada a um notebook já existente, identificado pelo título
    (não pelo notebook_id) — ex.: "Presidencia Funchal". Sem continuidade de
    conversa, sem prompt/persona injetada, sem streaming: a resposta volta
    inteira, de uma vez, em texto puro.
    """
    logger.info("Pergunta recebida para o notebook '%s'", req.notebook_title)

    try:
        return await ask_notebook(req.notebook_title, req.question)
    except NotebookNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AmbiguousNotebookTitleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao consultar o notebook via chat")
        raise HTTPException(status_code=500, detail=f"Erro ao consultar o notebook: {str(e)}")
