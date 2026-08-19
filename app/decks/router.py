"""
Endpoints do gerador de slides por IA.

Autenticação: mesmo middleware global (`x-api-key`) do resto do serviço —
sem dependency própria aqui, ver `validar_acesso` em main.py.
"""

import json
import logging
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.database import get_db_conn
from app.decks.repository import DeckJobRepository
from app.decks.storage import create_signed_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decks", tags=["decks"])


class CreateDeckJobRequest(BaseModel):
    user_id: UUID
    editorial_text: str = Field(min_length=1, description="Editorial colado pelo usuário no chat")
    client_id: Optional[UUID] = None


class StepStatus(BaseModel):
    step: str
    status: str
    attempt: int
    output_ref: Optional[str] = None
    error: Optional[str] = None


class DeckJobStatusResponse(BaseModel):
    id: UUID
    status: str
    cost_cents: int
    created_at: datetime
    steps: list[StepStatus]


class DeckJobSummary(BaseModel):
    """Um item da listagem (GET /decks) — só o suficiente pra montar uma lista, não o detalhe todo."""

    id: UUID
    status: str
    cost_cents: int
    created_at: datetime
    editorial_preview: str = Field(description="Primeiros ~80 caracteres do editorial, truncado no banco.")


class DeckJobListResponse(BaseModel):
    jobs: list[DeckJobSummary]


def _to_status_response(job, steps) -> DeckJobStatusResponse:
    return DeckJobStatusResponse(
        id=job["id"],
        status=job["status"],
        cost_cents=job["cost_cents"],
        created_at=job["created_at"],
        steps=[
            StepStatus(
                step=s["step"],
                status=s["status"],
                attempt=s["attempt"],
                output_ref=s["output_ref"],
                error=s["error"],
            )
            for s in steps
        ],
    )


@router.post("", response_model=DeckJobStatusResponse, status_code=201)
async def create_deck_job(req: CreateDeckJobRequest) -> DeckJobStatusResponse:
    """Cria o job e já enfileira as 4 etapas como 'pending'. Responde na hora — quem processa é o poller."""
    async for conn in get_db_conn():
        repo = DeckJobRepository(conn)
        job = await repo.create_job(req.user_id, req.editorial_text, req.client_id)
        steps = await repo.get_steps(job["id"])
        logger.info("Deck job criado — id=%s, user_id=%s", job["id"], req.user_id)
        return _to_status_response(job, steps)
    raise HTTPException(status_code=503, detail="Banco de dados indisponível")


@router.get("", response_model=DeckJobListResponse)
async def list_deck_jobs(
    user_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> DeckJobListResponse:
    """
    Lista os jobs de um usuário, mais recentes primeiro (2026-08-19) — sem
    isso, o frontend não tinha como recuperar o que já foi gerado depois de
    perder a sessão (só sabia o ID de um job se guardasse em algum lugar do
    lado do navegador, o que não sobrevive a logout/troca de aparelho). O
    backend passa a ser a fonte de verdade da lista.

    `user_id` obrigatório e não validado contra sessão nenhuma aqui — mesmo
    espírito do `POST /decks`: quem garante que é o usuário certo é a
    camada de auth do backend do frontend, não este serviço.
    """
    async for conn in get_db_conn():
        repo = DeckJobRepository(conn)
        rows = await repo.list_jobs_for_user(user_id, limit, offset)
        return DeckJobListResponse(jobs=[DeckJobSummary(**dict(r)) for r in rows])
    raise HTTPException(status_code=503, detail="Banco de dados indisponível")


@router.get("/{job_id}", response_model=DeckJobStatusResponse)
async def get_deck_job_status(job_id: UUID) -> DeckJobStatusResponse:
    async for conn in get_db_conn():
        repo = DeckJobRepository(conn)
        job = await repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job não encontrado")
        steps = await repo.get_steps(job_id)
        return _to_status_response(job, steps)
    raise HTTPException(status_code=503, detail="Banco de dados indisponível")


@router.get("/{job_id}/download")
async def download_deck(
    job_id: UUID,
    format: Literal["pdf", "pptx"] = Query(
        "pdf", description="Formato de saída — a escolha é do usuário, feita na hora do download, não na criação do job."
    ),
) -> RedirectResponse:
    """
    Redireciona pra uma URL assinada recém-gerada do arquivo pedido (PDF ou
    PPTX — os dois já foram gerados pela etapa `render`, ver steps.py). A URL
    não é guardada em lugar nenhum — expira em 1h por design, então é sempre
    gerada na hora, a partir do caminho estável salvo pela etapa `render`.
    """
    async for conn in get_db_conn():
        repo = DeckJobRepository(conn)
        steps = await repo.get_steps(job_id)
        render_step = next((s for s in steps if s["step"] == "render"), None)
        if render_step is None or render_step["status"] != "completed":
            raise HTTPException(
                status_code=404,
                detail="Deck ainda não está pronto para este job (etapa 'render' não concluída).",
            )
        object_path = json.loads(render_step["output_ref"])[format]
        signed_url = await create_signed_url(object_path)
        return RedirectResponse(url=signed_url)
    raise HTTPException(status_code=503, detail="Banco de dados indisponível")
