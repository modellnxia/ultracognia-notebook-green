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
