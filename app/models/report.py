from datetime import date as dt_date
from uuid import UUID

from pydantic import BaseModel, Field
from typing import Optional


class NotebookRequest(BaseModel):
    notebook_id: str = Field(
        "ID do notebook", description="ID do notebook no NotebookLM"
    )


class ReportRequest(BaseModel):
    """
    Requisição para geração de relatório.
    O notebook deve ter sido previamente preparado via /prepare-notebook.
    """

    notebook_id: str = Field(
        ..., title="ID do notebook", description="ID do notebook no NotebookLM"
    )
    notebook_title: Optional[str] = Field(
        None, description="Título do notebook. Se omitido, usa o notebook_id como fallback."
    )


class PrepareNotebookRequest(BaseModel):
    """
    Requisição para preparar um notebook no NotebookLM a partir de mensagens do banco.
    Se já existir um notebook para o usuário e data informados, retorna o ID existente (cache).
    """

    user_id: UUID = Field(..., description="UUID do usuário")
    start_date: dt_date = Field(..., description="Data das mensagens (YYYY-MM-DD) ou data inicial do range")
    end_date: Optional[dt_date] = Field(None, description="Data final do range. Se omitido, será igual a start_date.")
    force_recreate: bool = Field(
        False, description="Forçar recriação do notebook ignorando o cache"
    )


class PrepareNotebookResponse(BaseModel):
    notebook_id: str
    notebook_title: str
    from_cache: bool


class ReportResponse(BaseModel):
    notebook_id: str
    notebook_title: str
    report: str
    report_path: str


class NotebookDefaultResponse(BaseModel):
    notebook_id: str
    message: str
    status: bool
