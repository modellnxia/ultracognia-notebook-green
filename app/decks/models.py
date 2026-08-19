"""
Contrato DeckSpec — o elemento central do gerador de slides por IA.

Nenhum agente do pipeline gera slide diretamente. Os agentes produzem e
transformam este documento (JSON via Pydantic); só o renderizador (fatia 3+)
o converte em arquivo final (HTML/PDF). Isso permite regerar todos os decks
quando a marca mudar, sem reexecutar IA e sem pagar token de novo.

`LayoutId` é fechado de propósito — o agente `structure` escolhe entre os
layouts existentes, nunca inventa um novo, nunca define cor ou fonte fora do
`Theme`. Começa com 6; cresce depois que o pipeline estiver provado
end-to-end (ver README.md desta pasta). `infographic` (2026-08-19) é o 7º —
ver decisão de mirar no padrão visual denso (eyebrow + ilustração hero +
painéis estruturados + citação), a partir de referências reais fornecidas
pelo usuário (few-shot, ver structure.py).
"""

from enum import Enum
from typing import Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class LayoutId(str, Enum):
    COVER = "cover"
    SECTION_BREAK = "section-break"
    TITLE_BULLETS = "title-bullets"
    TWO_COLUMN = "two-column"
    DIAGRAM_FULL = "diagram-full"
    CLOSING = "closing"
    INFOGRAPHIC = "infographic"


class Theme(BaseModel):
    """
    Identidade visual de UM deck. Nunca decidida pelo `render` — só consumida
    por ele. Pode vir de um agente de IA (decisão editorial, ver README) ou
    ser fixada manualmente por um `client_id` no futuro.
    """

    palette: dict[str, str] = Field(
        description=(
            "Cores nomeadas por papel semântico, não por slide — ex.: "
            '{"primary": "#1A2B3C", "background": "#FFFFFF", "text": "#111111", '
            '"accent": "#FF6B4A"}. Contraste texto/fundo é validado no QA '
            "(fatia 8), não aqui."
        )
    )
    font_stack: str = Field(description='Ex.: "Inter, system-ui, sans-serif"')
    logo_url: Optional[str] = None

    @field_validator("palette")
    @classmethod
    def requires_core_roles(cls, v: dict[str, str]) -> dict[str, str]:
        required = {"primary", "background", "text"}
        missing = required - v.keys()
        if missing:
            raise ValueError(f"palette sem papéis obrigatórios: {sorted(missing)}")
        return v


# ── Blocos de conteúdo tipado — nunca HTML livre ────────────────────────────
# O agente `structure` só pode produzir estes tipos. Isso é o que garante que
# o `render` nunca precisa "adivinhar" o que fazer com texto arbitrário.


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    text: str
    level: int = Field(default=2, ge=1, le=3)


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class BulletListBlock(BaseModel):
    type: Literal["bullets"] = "bullets"
    # Limite de 7 alinhado com a regra de QA visual (fatia 8): mais que isso
    # e o slide "estoura" — melhor rejeitar aqui do que descobrir no QA.
    items: list[str] = Field(min_length=1, max_length=7)


class QuoteBlock(BaseModel):
    type: Literal["quote"] = "quote"
    text: str
    attribution: Optional[str] = None


class Panel(BaseModel):
    """Um mini-card dentro de um PanelListBlock — título curto + descrição curta, nunca texto livre longo."""

    heading: str
    text: str


class PanelListBlock(BaseModel):
    """
    Layout `infographic` (2026-08-19): 2 a 4 mini-cards lado a lado, cada um
    com um título curto e uma descrição — o padrão visual dos exemplos
    few-shot (ver structure.py). Diferente de `BulletListBlock`: cada item
    aqui tem título PRÓPRIO, não é uma lista simples de frases.
    """

    type: Literal["panels"] = "panels"
    panels: list[Panel] = Field(min_length=2, max_length=4)


Block = Union[HeadingBlock, ParagraphBlock, BulletListBlock, QuoteBlock, PanelListBlock]


class SlideAsset(BaseModel):
    kind: Literal["diagram", "image"]
    # Antes da etapa `assets` rodar, isso é uma descrição/prompt (texto).
    # Depois, vira a chave no object storage. O renderizador só aceita a
    # segunda forma — ver validação em `render` (fatia 3).
    ref: str


class Slide(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    layout: LayoutId
    title: str
    body: list[Block] = Field(default_factory=list)
    asset: Optional[SlideAsset] = None
    notes: Optional[str] = None
    # Os dois abaixo só têm efeito visual no layout `infographic` (2026-08-19)
    # — em outros layouts o render simplesmente ignora, se vierem preenchidos.
    eyebrow: Optional[str] = Field(
        default=None, description='Etiqueta de categoria curta, ex.: "Gestão de Capital Humano | Retenção"'
    )
    citation: Optional[str] = Field(
        default=None, description='Rodapé citando um framework/fonte, ex.: "Teoria dos Jogos — MIT Sloan"'
    )


class DeckSpec(BaseModel):
    version: Literal["1.0"] = "1.0"
    theme: Theme
    slides: list[Slide] = Field(min_length=1)

    @field_validator("slides")
    @classmethod
    def unique_slide_ids(cls, v: list[Slide]) -> list[Slide]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("slide ids devem ser únicos dentro do DeckSpec")
        return v
