from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NotebookChatRequest(BaseModel):
    """
    Requisição de chat isolado (2026-09-12) — cada pergunta é independente,
    sem continuidade de conversa (sem conversation_id), sem prompt/persona
    injetada. O notebook é identificado pelo TÍTULO exibido na tela do
    NotebookLM (ex.: "Presidencia Funchal"), não pelo notebook_id — a conta
    usada é sempre a mesma (eaespiritual@gmail.com, via master-token já
    configurado, mesmo mecanismo de app/core/notebooklm_auth.py).
    """

    notebook_title: str = Field(
        ..., min_length=1,
        description='Título exato do notebook no NotebookLM, ex.: "Presidencia Funchal".',
    )
    question: str = Field(..., min_length=1, description="Pergunta a ser feita ao notebook.")


class NotebookChatResponse(BaseModel):
    answer: str = Field(description="Resposta do NotebookLM, em texto puro.")


class NoteInfo(BaseModel):
    """Nota (conteúdo de usuário/chat salvo) existente no notebook — content já vem inteiro, sem chamada extra."""

    id: str
    title: str
    created_at: Optional[datetime] = None
    content: str


class ReportArtifactInfo(BaseModel):
    """
    Artefato de relatório (aba Studio) existente no notebook. `content` só
    vem preenchido se o download funcionou (ver notebook_chat_service.py) —
    `None` indica falha ao baixar esse relatório específico, não ausência
    de conteúdo.
    """

    id: str
    title: str
    status: int = Field(description="1=processing, 2=pending, 3=completed, 4=failed (direto da lib).")
    created_at: Optional[datetime] = None
    url: Optional[str] = None
    generation_prompt: Optional[str] = Field(
        default=None, description="O prompt/pergunta que gerou este relatório, quando disponível."
    )
    content: Optional[str] = None


class NotebookStudioItemsResponse(BaseModel):
    """
    Investigação (2026-09-14) pro futuro endpoint de download — lista tudo
    que já existe na aba Studio do notebook, com conteúdo já baixado.
    """

    notes: list[NoteInfo]
    reports: list[ReportArtifactInfo]
