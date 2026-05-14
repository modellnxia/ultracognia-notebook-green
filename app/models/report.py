from pydantic import BaseModel, Field
from typing import Optional


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
