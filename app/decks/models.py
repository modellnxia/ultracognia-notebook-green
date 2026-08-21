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

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class LayoutId(str, Enum):
    COVER = "cover"
    SECTION_BREAK = "section-break"
    TITLE_BULLETS = "title-bullets"
    TWO_COLUMN = "two-column"
    DIAGRAM_FULL = "diagram-full"
    CLOSING = "closing"
    INFOGRAPHIC = "infographic"


# Luminância relativa máxima aceita pro papel "background" da paleta — guardrail
# "criativo com margem" (ver FEWSHOT_RULEBOOK.md): a IA continua livre pra
# escolher QUALQUER matiz (navy, verde-escuro, bordô, petróleo...), mas um
# valor claro/branco é rejeitado aqui, estruturalmente, não só "pedido" no
# texto do prompt. Fecha o bug real de produção (fundo quase-branco) achado
# em 2026-08-19 — nenhum dos 59 exemplos de referência tem fundo principal
# claro. 0.25 cobre toda a faixa observada nos exemplos (a maioria fica bem
# abaixo disso, tipo 0.01-0.05) e ainda deixa cinza-médio de fora.
_MAX_BACKGROUND_LUMINANCE = 0.25


def _relative_luminance(hex_color: str) -> float:
    """Luminância relativa (sRGB, fórmula WCAG) — 0 = preto, 1 = branco."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def _linearize(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = _linearize(r), _linearize(g), _linearize(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Razão de contraste WCAG entre 2 cores — 1:1 (idênticas) a 21:1 (preto/branco)."""
    l_a, l_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


# Achado real em produção (2026-08-21 (7)): a IA escolheu background=#081421 e
# primary=#0A192F — tons quase idênticos (contraste 1.05:1). O CSS usa
# `primary` como cor de destaque/fundo dos slides de tela cheia (cover/
# section-break/closing, onde o CSS INVERTE: fundo do tema vira cor do
# texto) — com as duas cores próximas assim, esse texto fica ilegível.
# Diferente de `background_must_be_dark` (guardrail de ESTILO, gated pelo
# checkbox): isso aqui é garantia de RENDERIZAÇÃO LEGÍVEL, não opinião
# estética — fica ativo sempre, independente do checkbox.
_MIN_BACKGROUND_PRIMARY_CONTRAST = 3.0


class Theme(BaseModel):
    """
    Identidade visual de UM deck. Nunca decidida pelo `render` — só consumida
    por ele. Pode vir de um agente de IA (decisão editorial, ver README) ou
    ser fixada manualmente por um `client_id` no futuro.
    """

    palette: dict[str, str] = Field(
        description=(
            "Cores nomeadas por papel semântico, não por slide — ex.: "
            '{"primary": "#1A2B3C", "background": "#0D1B2E", "text": "#F2F2F2", '
            '"accent": "#FF6B4A"}. Contraste texto/fundo é validado no QA '
            "(fatia 8), não aqui. `background` precisa ser um tom escuro — "
            "ver FEWSHOT_RULEBOOK.md."
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

    @field_validator("palette")
    @classmethod
    def background_must_be_dark(cls, v: dict[str, str], info: ValidationInfo) -> dict[str, str]:
        """
        Guardrail "criativo com margem" — só ativo quando `apply_style_guardrails`
        estiver True no contexto de validação (2026-08-21, tarefa 1: checkbox
        na tela liga/desliga o rulebook inteiro). Sem contexto explícito,
        assume True (comportamento de sempre, retrocompatível — todo código
        que já chama `Theme(...)`/`DeckSpec.model_validate(...)` sem passar
        `context=` continua exigindo fundo escuro, como sempre foi).
        """
        if info.context and info.context.get("enforce_dark_background") is False:
            return v

        background = v.get("background")
        if background is None:
            return v
        try:
            luminance = _relative_luminance(background)
        except (ValueError, IndexError) as exc:
            raise ValueError(f'palette["background"] não é um hex válido: {background!r}') from exc
        if luminance >= _MAX_BACKGROUND_LUMINANCE:
            raise ValueError(
                f'palette["background"] = {background!r} é claro demais (luminância '
                f"{luminance:.2f} ≥ {_MAX_BACKGROUND_LUMINANCE}) — os exemplos de referência "
                "usam sempre um fundo escuro (navy, petróleo, grafite, bordô-escuro etc.). "
                "Escolha um tom escuro coerente com o tema do conteúdo, nunca branco/claro."
            )
        return v

    @field_validator("palette")
    @classmethod
    def background_and_primary_must_be_distinguishable(cls, v: dict[str, str]) -> dict[str, str]:
        """
        Sempre ativo — não é opinião de estilo (ver comentário de
        `_MIN_BACKGROUND_PRIMARY_CONTRAST`), é garantia de que o `primary`
        não vira invisível quando o CSS o usa como cor de texto (slides de
        tela cheia — cover/section-break/closing invertem fundo↔primary).
        """
        background, primary = v.get("background"), v.get("primary")
        if background is None or primary is None:
            return v
        try:
            ratio = _contrast_ratio(background, primary)
        except (ValueError, IndexError):
            return v  # hex inválido já é pego pelo validador de fundo escuro, não duplica o erro aqui
        if ratio < _MIN_BACKGROUND_PRIMARY_CONTRAST:
            raise ValueError(
                f'palette["background"] ({background!r}) e palette["primary"] ({primary!r}) '
                f"têm contraste baixo demais entre si ({ratio:.2f}:1, mínimo "
                f"{_MIN_BACKGROUND_PRIMARY_CONTRAST}:1) — o CSS usa 'primary' como cor de "
                "texto nos slides de tela cheia (cover/section-break/closing), então as duas "
                "cores ficarem parecidas deixa esse texto ilegível. Escolha um 'primary' "
                "visivelmente diferente do 'background', não só uma variação sutil do mesmo tom."
            )
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


# ── Peças de composição do layout `infographic` (2026-08-21) ───────────────
# Vocabulário fechado extraído dos 59 exemplos de referência reais — ver
# FEWSHOT_RULEBOOK.md pra origem de cada opção. A IA (agente `structure`)
# escolhe uma COMBINAÇÃO dessas peças por slide; ela pode compor algo que
# nunca apareceu literalmente em nenhum dos 59 exemplos, mas só usando peças
# que já existem aqui — nunca inventa um valor fora do Literal (Pydantic
# rejeita antes de chegar no render). Todos opcionais e `None` por padrão:
# um slide sem nenhuma peça preenchida cai no visual `infographic` de sempre
# (retrocompatível com decks/testes anteriores a esta mudança).
IllustrationPosition = Literal["left", "right", "background", "none"]
EyebrowStyle = Literal["chamfered-left", "pill-left", "pill-center", "none"]
PanelStyle = Literal["void-frame", "bordered-card", "borderless-icon"]
Arrangement = Literal["stacked", "row", "comparison-columns", "sequence-numbered", "sequence-lettered"]
CitationStyle = Literal["bar-circuit-corners", "bordered-card", "split-two"]


class Slide(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    layout: LayoutId
    title: str
    body: list[Block] = Field(default_factory=list)
    asset: Optional[SlideAsset] = None
    notes: Optional[str] = None
    # Os campos abaixo só têm efeito visual no layout `infographic` (2026-08-19)
    # — em outros layouts o render simplesmente ignora, se vierem preenchidos.
    eyebrow: Optional[str] = Field(
        default=None, description='Etiqueta de categoria curta, ex.: "Gestão de Capital Humano | Retenção"'
    )
    citation: Optional[str] = Field(
        default=None, description='Rodapé citando um framework/fonte, ex.: "Teoria dos Jogos — MIT Sloan"'
    )
    illustration_position: Optional[IllustrationPosition] = Field(
        default=None, description="Onde a ilustração do slide fica — ver FEWSHOT_RULEBOOK.md. None = padrão (left)."
    )
    eyebrow_style: Optional[EyebrowStyle] = Field(
        default=None, description="Formato visual do badge de eyebrow. None = padrão (chamfered-left)."
    )
    panel_style: Optional[PanelStyle] = Field(
        default=None, description="Formato visual de cada painel do bloco 'panels'. None = padrão (bordered-card)."
    )
    arrangement: Optional[Arrangement] = Field(
        default=None, description="Como os painéis se organizam no slide. None = padrão (row)."
    )
    citation_style: Optional[CitationStyle] = Field(
        default=None, description="Formato visual da citação de rodapé. None = padrão (bar-circuit-corners)."
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
