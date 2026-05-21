from datetime import date as dt_date
from uuid import UUID

from pydantic import BaseModel, Field
from typing import Optional


class NotebookRequest(BaseModel):
    notebook_id: str = Field(
        "ID do notebook", description="ID do notebook no NotebookLM"
    )


class ReportRequest(BaseModel):
    messages: list[str] = Field(
        ..., title="Mensagens", min_length=1, description="Lista de mensagens com a LLM"
    )

    notebook_title: str = Field(
        "Relatório de Conversa",
        title="Título do notebook",
        description="Título base do notebook a ser criado no NotebookLM",
    )

    notebook_id: Optional[str] = Field(
        None, title="ID do notebook", description="ID do notebook no NotebookLM"
    )


class ReportResponse(BaseModel):
    notebook_id: str
    notebook_title: str
    report: str
    report_path: str


class NotebookDefaultResponse(BaseModel):
    notebook_id: str
    message: str
    status: bool


class UserDateReportRequest(BaseModel):
    user_id: UUID = Field(..., description="UUID do usuário")
    target_date: dt_date = Field(..., description="Data das mensagens (YYYY-MM-DD)")
    notebook_title: str = Field(
        "Relatório de Conversa",
        description="Título base do notebook a ser criado no NotebookLM",
    )
    notebook_id: Optional[str] = Field(
        None, description="Reutilizar notebook existente (opcional)"
    )
    force_recreate: bool = Field(
        False, description="Forçar recriação do relatório ignorando o cache"
    )
