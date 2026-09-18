"""
Router novo e separado (2026-09-12, pedido explícito do usuário — não
misturar com app/routers/report.py) pro chat isolado com um notebook do
NotebookLM. Ver app/services/notebook_chat_service.py pra lógica completa.
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from notebooklm.exceptions import ArtifactNotFoundError

from app.models.notebook_chat import (
    NotebookChatRequest,
    NotebookChatResponse,
    NotebookStudioItemsResponse,
)
from app.services.notebook_chat_service import (
    AmbiguousNotebookTitleError,
    ArtifactContentUnavailableError,
    NotebookNotFoundError,
    ask_notebook,
    get_report_content,
    list_studio_items,
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


@router.get("/studio-items", response_model=NotebookStudioItemsResponse)
async def list_studio_items_endpoint(notebook_title: str) -> NotebookStudioItemsResponse:
    """
    Lista notas e relatórios já existentes na aba Studio do notebook, com o
    conteúdo já baixado quando possível — a UI escolhe um `id` daqui e
    chama `GET /notebook-chat/reports/download` com ele pra baixar de
    verdade (esta rota é só listagem/preview, não é o download final).
    """
    logger.info("Listando itens do Studio do notebook '%s'", notebook_title)

    try:
        return await list_studio_items(notebook_title)
    except NotebookNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AmbiguousNotebookTitleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao listar itens do Studio")
        raise HTTPException(status_code=500, detail=f"Erro ao listar itens do Studio: {str(e)}")


@router.get("/reports/download")
async def download_report_endpoint(notebook_title: str, artifact_id: str) -> Response:
    """
    Download do conteúdo de UM relatório/artefato específico, escolhido
    pelo `artifact_id` que já vem em `GET /notebook-chat/studio-items`.
    Serve tanto markdown quanto binário (PDF) — achado 2026-09-17.

    Busca o conteúdo real (caminho oficial da lib + fallback — ver
    `notebook_chat_service.py::_download_artifact_content`) e devolve como
    arquivo pra download de uma vez só — sem streaming nosso, é a resposta
    completa (mesmo padrão do resto da API, decisão do usuário), com o
    `Content-Type` real do arquivo (não fixo em markdown como antes).
    """
    logger.info("Download pedido — notebook '%s', artifact_id=%s", notebook_title, artifact_id)

    try:
        content, filename, mime_type = await get_report_content(notebook_title, artifact_id)
    except NotebookNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AmbiguousNotebookTitleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ArtifactNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Relatório não encontrado nesse notebook: {e}")
    except ArtifactContentUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao baixar relatório")
        raise HTTPException(status_code=500, detail=f"Erro ao baixar relatório: {str(e)}")

    # RFC 6266 (filename* com percent-encoding) — títulos reais têm acento
    # (ex.: "relatório-...") e Content-Disposition clássico não aceita não-ASCII direto.
    safe_filename = quote(filename)
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
    )
