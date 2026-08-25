"""
Classificador de layout (Fatia C, 2026-08-24) — resposta à visão de
arquitetura do Ramon/TYPOGRAPHOS-Σ: uma camada separada e DETERMINÍSTICA que
sugere o arquétipo mais adequado a partir de métricas do CONTEÚDO já gerado
(presença de imagem/diagrama/painéis, nº de blocos) — inspecionável e
testável isoladamente, sem chamada de IA nenhuma. Mesmo espírito do exemplo
dado por Ramon/TYPOGRAPHOS-Σ ("Título: 62 caracteres... → LAYOUT_X").

Escopo desta fatia, deliberadamente mais cauteloso que o plano original de 3
fatias: em vez de tentar prever o layout ANTES da chamada ao LLM (exigiria
detectar limites de slide dentro do editorial em texto livre — formato não
documentado/garantido, arriscado construir um parser sobre uma suposição não
validada), o classificador roda DEPOIS, contra o `DeckSpec` já gerado (dado
confiável, vem do schema tipado, não de parsing de texto livre) — usado como
um 5º check não bloqueante do QA (ver `qa.py::_check_layout_suggestion`),
mesma filosofia dos outros 4 checks já existentes. Não substitui a decisão do
LLM — só a torna auditável.

Vocabulário do classificador é mais estreito que o `LayoutId` inteiro — só
opina sobre os 3 layouts "de conteúdo" mais óbvios de inferir por métrica
(`title-bullets`, `diagram-full`, `infographic`). `cover`/`section-break`/
`closing` são escolhas POSICIONAIS (primeiro/último slide, transição), não
guiadas por conteúdo, e `two-column` depende de uma noção de "comparação"
que não dá pra inferir com segurança só de contagem de blocos — os dois
ficam fora do escopo do classificador de propósito (documentado aqui, não é
lacuna esquecida).
"""

from app.decks.models import LayoutId, Slide

# Layouts sobre os quais o classificador tem opinião — ver docstring acima.
CLASSIFIABLE_LAYOUTS = {LayoutId.TITLE_BULLETS, LayoutId.DIAGRAM_FULL, LayoutId.INFOGRAPHIC}


def suggest_layout(*, has_diagram: bool, has_image: bool, has_panels: bool, body_block_count: int) -> LayoutId:
    """
    Sugestão determinística a partir de sinais de conteúdo — regras simples e
    conservadoras nesta primeira versão (ajustar com uso real, ver docstring
    do módulo):
      - tem diagrama → `diagram-full` (é literalmente o layout feito pra isso).
      - tem painéis, OU tem imagem com 2+ blocos de corpo → `infographic`
        (o padrão denso — eyebrow/ilustração/painéis/citação — pede conteúdo
        suficiente pra preencher, não só uma frase solta).
      - caso contrário → `title-bullets` (o "piso" de conteúdo simples).
    """
    if has_diagram:
        return LayoutId.DIAGRAM_FULL
    if has_panels or (has_image and body_block_count >= 2):
        return LayoutId.INFOGRAPHIC
    return LayoutId.TITLE_BULLETS


def slide_signals(slide: Slide) -> dict:
    """Extrai as métricas de UM slide já gerado — usadas tanto pra sugerir quanto pra comparar contra a escolha real."""
    return {
        "has_diagram": slide.asset is not None and slide.asset.kind == "diagram",
        "has_image": slide.asset is not None and slide.asset.kind == "image",
        "has_panels": any(b.type == "panels" for b in slide.body),
        "body_block_count": len(slide.body),
    }


def classify(slide: Slide) -> LayoutId:
    """Sugestão pro slide já gerado — conveniência que junta `slide_signals` + `suggest_layout`."""
    return suggest_layout(**slide_signals(slide))
