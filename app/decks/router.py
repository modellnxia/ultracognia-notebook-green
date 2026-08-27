"""
Endpoints do gerador de slides por IA.

Autenticação: mesmo middleware global (`x-api-key`) do resto do serviço —
ver `validar_acesso` em main.py. Exceção: `/decks/themes` (2026-08-24) exige
uma segunda chave (`require_theme_admin_key`) por cima do `x-api-key` global —
gerenciar a marca de um cliente é ação administrativa, não da tela de
geração do dia a dia.
"""

import json
import logging
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.database import get_db_conn
from app.core.settings import settings
from app.decks.models import Theme
from app.decks.repository import DeckJobRepository
from app.decks.storage import create_signed_url
from app.decks.theme_repository import ThemeRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decks", tags=["decks"])


async def require_theme_admin_key(x_theme_admin_key: Optional[str] = Header(default=None)) -> None:
    """
    Gate extra (2026-08-24, Fatia A) só pros endpoints de administração da
    Theme Library — além do `x-api-key` global que todo request já precisa
    (`validar_acesso` em main.py), gerenciar a marca de um cliente exige uma
    segunda chave (`DECKS_THEME_ADMIN_KEY`). Fechado por padrão: sem a env
    var configurada, ninguém consegue usar essas rotas (não abre "sem querer"
    só porque o serviço está no ar) — decisão do usuário em 2026-08-24.
    """
    if not settings.DECKS_THEME_ADMIN_KEY:
        raise HTTPException(
            status_code=503,
            detail="Theme Library desabilitada — DECKS_THEME_ADMIN_KEY não configurada neste ambiente.",
        )
    if x_theme_admin_key != settings.DECKS_THEME_ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Chave de administração da Theme Library inválida ou ausente.")


class CreateDeckJobRequest(BaseModel):
    user_id: UUID
    editorial_text: str = Field(min_length=1, description="Editorial colado pelo usuário no chat")
    client_id: Optional[UUID] = None
    llm_provider: Optional[Literal["gemini", "deepseek", "openai", "openrouter"]] = Field(
        default=None,
        description=(
            "Escolha explícita do combo na tela — cada opção roda ponta a ponta num provider só, "
            "texto E imagem: 'gemini' usa Gemini pros dois; 'deepseek' usa DeepSeek pro texto + OpenAI "
            "pra imagem (DeepSeek não tem produto de imagem); 'openai' usa OpenAI pros dois; "
            "'openrouter' usa OpenRouter pros dois (reativado no combo em 2026-08-27 — chave nova do "
            "cliente, com endpoint de imagem próprio, validado manualmente). Omitir = fallback "
            "automático de sempre entre os providers ativos, imagem cai no default da env var."
        ),
    )
    apply_style_guardrails: bool = Field(
        default=False,
        description=(
            "Checkbox da tela — desligado (padrão, decisão do usuário em 2026-08-21): só o editorial "
            "guia o resultado, extraído pro nosso schema de JSON e mandado pro agente sem nenhuma "
            "opinião de estilo nossa — inclusive paleta clara é aceita se o editorial pedir. Ligado: "
            "aplica o rulebook visual interno (vocabulário de composição, exemplos de referência, "
            "fundo obrigatoriamente escuro) — só quando o usuário marcar explicitamente."
        ),
    )
    theme_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Theme Library (2026-08-24) — quando informado, prende a paleta/fonte do deck a um tema "
            "salvo (ver POST /decks/themes) em vez de deixar o LLM improvisar a identidade visual a "
            "cada geração. Omitir mantém o comportamento de sempre (LLM decide)."
        ),
    )


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
    llm_provider: Optional[str] = None
    apply_style_guardrails: bool = False
    theme_id: Optional[UUID] = None
    steps: list[StepStatus]


class DeckJobSummary(BaseModel):
    """Um item da listagem (GET /decks) — só o suficiente pra montar uma lista, não o detalhe todo."""

    id: UUID
    status: str
    cost_cents: int
    created_at: datetime
    llm_provider: Optional[str] = None
    apply_style_guardrails: bool = False
    editorial_preview: str = Field(description="Primeiros ~80 caracteres do editorial, truncado no banco.")


class DeckJobListResponse(BaseModel):
    jobs: list[DeckJobSummary]


class ThemeRequest(BaseModel):
    """
    Theme Library (2026-08-24, Fatia A) — cria ou atualiza (upsert por
    `client_id` + `name`) um tema de marca nomeado. Endpoint atrás de
    `require_theme_admin_key`: gerenciar a identidade visual de um cliente
    não é ação da tela de geração do dia a dia.
    """

    name: str = Field(min_length=1, description='Nome do tema, único por cliente — ex.: "padrao".')
    client_id: Optional[UUID] = None
    palette: dict[str, str] = Field(
        description='Mesmo formato de Theme.palette — ex.: {"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"}.'
    )
    font_stack: str = Field(description='Ex.: "Inter, system-ui, sans-serif"')
    logo_url: Optional[str] = None

    @field_validator("palette")
    @classmethod
    def palette_must_be_a_valid_theme(cls, v: dict[str, str]) -> dict[str, str]:
        """
        Reaproveita as validações de `Theme` (papéis obrigatórios, contraste
        background/primary) — sempre ativas aqui, mesmo padrão de qualquer
        outro tema. `enforce_dark_background=False` de propósito: um tema
        salvo é escolha humana intencional de marca, pode ser claro (decisão
        do usuário, 2026-08-24) — diferente do LLM improvisando, que segue a
        regra normal quando o guardrail de estilo está ligado.
        """
        try:
            Theme.model_validate(
                {"palette": v, "font_stack": "placeholder"}, context={"enforce_dark_background": False}
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return v


class ThemeResponse(BaseModel):
    id: UUID
    client_id: Optional[UUID] = None
    name: str
    palette: dict[str, str]
    font_stack: str
    logo_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ThemeListResponse(BaseModel):
    themes: list[ThemeResponse]


def _to_theme_response(row) -> ThemeResponse:
    return ThemeResponse(
        id=row["id"],
        client_id=row["client_id"],
        name=row["name"],
        palette=json.loads(row["palette"]),
        font_stack=row["font_stack"],
        logo_url=row["logo_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_status_response(job, steps) -> DeckJobStatusResponse:
    return DeckJobStatusResponse(
        id=job["id"],
        status=job["status"],
        cost_cents=job["cost_cents"],
        created_at=job["created_at"],
        llm_provider=job["llm_provider"],
        apply_style_guardrails=job["apply_style_guardrails"],
        theme_id=job["theme_id"],
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
        job = await repo.create_job(
            req.user_id,
            req.editorial_text,
            req.client_id,
            req.llm_provider,
            req.apply_style_guardrails,
            theme_id=req.theme_id,
        )
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


@router.post("/themes", response_model=ThemeResponse, status_code=201, dependencies=[Depends(require_theme_admin_key)])
async def create_theme(req: ThemeRequest) -> ThemeResponse:
    """
    Cria ou atualiza um tema nomeado (upsert por `client_id` + `name`). Job
    nenhum é criado aqui — só grava o tema, pra ser referenciado depois via
    `theme_id` em `POST /decks`. Precisa passar o header `X-Theme-Admin-Key`
    (ver `require_theme_admin_key`).
    """
    async for conn in get_db_conn():
        repo = ThemeRepository(conn)
        row = await repo.create_theme(req.name, req.palette, req.font_stack, req.client_id, req.logo_url)
        logger.info("Tema salvo — id=%s, client_id=%s, name=%s", row["id"], req.client_id, req.name)
        return _to_theme_response(row)
    raise HTTPException(status_code=503, detail="Banco de dados indisponível")


@router.get("/themes", response_model=ThemeListResponse, dependencies=[Depends(require_theme_admin_key)])
async def list_themes(client_id: Optional[UUID] = None) -> ThemeListResponse:
    """Lista temas salvos, opcionalmente filtrando por `client_id`."""
    async for conn in get_db_conn():
        repo = ThemeRepository(conn)
        rows = await repo.list_themes(client_id)
        return ThemeListResponse(themes=[_to_theme_response(r) for r in rows])
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
