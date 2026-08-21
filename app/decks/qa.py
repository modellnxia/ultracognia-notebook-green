"""
QA visual — fatia 7. Checks determinísticos, sem IA e sem custo (decisão do
usuário em 2026-08-18: preferir isso a uma revisão via LLM, que custaria
token por slide e traria mais uma dependência de quota como a da fatia 6).

Quatro checks, cada um cobrindo um jeito real do deck sair errado:
  1. Overflow de texto — conteúdo maior que a caixa fixa do slide (1280x720),
     medido de verdade no Chromium via Playwright (mesma técnica de
     diagrams.py), não estimado por contagem de caracteres.
  2. Contraste de cor — fórmula de luminância relativa do WCAG 2.0, aplicada
     aos dois pares de cor que o template realmente usa como texto (não é
     uma lib de acessibilidade nova, é ~15 linhas de matemática).
  3. Asset não resolvido — slide com `asset` cujo `ref` ainda é o prompt/
     sintaxe original (a etapa `assets` não conseguiu resolver), o que
     significa que o PDF final mostra o placeholder tracejado no lugar da
     imagem/diagrama.
  4. Vão vertical vazio (2026-08-21, tarefa 3) — nasceu de um achado real
     comparando nosso resultado com uma referência: a coluna de conteúdo do
     layout `infographic` podia deixar um vão enorme entre os painéis e a
     citação (a citação sempre ancora no rodapé via `margin-top: auto`,
     então conteúdo curto empurra ela pra baixo e sobra um vão no meio).
     Medido de verdade no Chromium — maior distância entre dois elementos
     consecutivos dentro de `.infographic__content`.

Decisão do usuário (2026-08-18): isso é só um AVISO, nunca bloqueia o job —
mesma filosofia de resiliência do resto do pipeline (assets quebrados também
nunca derrubam o job inteiro). Quem decide o que fazer com os `issues` é o
frontend, lendo o `output_ref` desta etapa.
"""

from app.decks.models import DeckSpec, Theme
from app.decks.render import render_deck_html
from playwright.async_api import async_playwright

# WCAG AA pra "texto grande" (18.66px+, ou 14px+ em negrito) — todo texto
# deste deck se qualifica (corpo é 22px, títulos são 40px+), por isso 3.0 é
# o limiar certo aqui, não o 4.5 de texto normal.
_MIN_CONTRAST_RATIO = 3.0

# Vão vertical máximo aceito entre dois elementos consecutivos dentro de
# `.infographic__content` antes de virar aviso — slide tem 720px de altura
# total; um vão de 130px+ já é visualmente perceptível como "espaço vazio
# no meio do slide" (achado real de 2026-08-21, comparando com referência).
_MAX_VERTICAL_GAP_PX = 130


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _channel(r), _channel(g), _channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    l_a, l_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


def _check_contrast(theme: Theme) -> list[dict]:
    """
    Os dois pares de cor que o template (deck.html.jinja2) realmente usa
    como texto-sobre-fundo: `text` sobre `background` nos slides normais, e
    `background` sobre `primary` nos slides de tela cheia (cover/
    section-break/closing, onde o CSS inverte: a cor de fundo do tema vira a
    cor do texto ali).
    """
    palette = theme.palette
    pairs = [
        ("text", "background", palette.get("text"), palette.get("background")),
        ("background", "primary", palette.get("background"), palette.get("primary")),
    ]
    issues = []
    for fg_name, bg_name, fg, bg in pairs:
        if not fg or not bg:
            continue
        ratio = _contrast_ratio(fg, bg)
        if ratio < _MIN_CONTRAST_RATIO:
            issues.append(
                {
                    "slide_id": None,  # é do tema inteiro, não de um slide específico
                    "kind": "low_contrast",
                    "message": (
                        f"Contraste entre '{fg_name}' ({fg}) e '{bg_name}' ({bg}) "
                        f"é {ratio:.2f}:1 — abaixo do mínimo recomendado ({_MIN_CONTRAST_RATIO}:1)."
                    ),
                }
            )
    return issues


def _check_unresolved_assets(deck: DeckSpec) -> list[dict]:
    """Mesma heurística de `steps.py::_with_resolved_asset_urls` pra saber se um asset foi resolvido."""
    issues = []
    for slide in deck.slides:
        asset = slide.asset
        if asset is not None and "/assets/" not in asset.ref and not asset.ref.startswith("http"):
            issues.append(
                {
                    "slide_id": slide.id,
                    "kind": "unresolved_asset",
                    "message": (
                        f"Asset ({asset.kind}) do slide não foi resolvido — o PDF final "
                        "mostra um placeholder no lugar da imagem/diagrama."
                    ),
                }
            )
    return issues


async def _check_overflow(html: str) -> list[dict]:
    """
    Mede de verdade se o conteúdo de algum `.slide` ultrapassa a caixa fixa
    de 1280x720 — `scrollHeight`/`scrollWidth` continuam reportando o
    tamanho real do conteúdo mesmo com `overflow: hidden` no CSS, então isso
    pega exatamente o que ficaria cortado visualmente no PDF.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="domcontentloaded")
            results = await page.eval_on_selector_all(
                ".slide",
                """(slides) => slides.map(s => ({
                    id: s.id,
                    overflowing: s.scrollHeight > s.clientHeight + 2 || s.scrollWidth > s.clientWidth + 2,
                }))""",
            )
        finally:
            await browser.close()

    return [
        {
            "slide_id": r["id"],
            "kind": "overflow",
            "message": (
                "O conteúdo deste slide ultrapassa a área visível (1280x720) — "
                "pode aparecer cortado no PDF."
            ),
        }
        for r in results
        if r["overflowing"]
    ]


async def _check_sparse_layout(html: str) -> list[dict]:
    """
    Mede o maior vão vertical entre elementos consecutivos dentro de
    `.infographic__content` (eyebrow/título/subtítulo/painéis/citação) — um
    vão grande ali é o sintoma visual de "conteúdo curto demais, sobrou
    espaço vazio no meio do slide" (achado real de 2026-08-21). Só se aplica
    a slides `infographic` — os outros layouts não têm essa estrutura.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="domcontentloaded")
            results = await page.eval_on_selector_all(
                ".infographic__content",
                """(contents) => contents.map(content => {
                    const slide = content.closest('.slide');
                    const children = Array.from(content.children);
                    let maxGap = 0;
                    for (let i = 1; i < children.length; i++) {
                        const prev = children[i - 1].getBoundingClientRect();
                        const curr = children[i].getBoundingClientRect();
                        const gap = curr.top - prev.bottom;
                        if (gap > maxGap) maxGap = gap;
                    }
                    return { id: slide ? slide.id : null, maxGap };
                })""",
            )
        finally:
            await browser.close()

    return [
        {
            "slide_id": r["id"],
            "kind": "sparse_layout",
            "message": (
                f"Vão vertical de {round(r['maxGap'])}px entre elementos deste slide "
                f"— acima do limite ({_MAX_VERTICAL_GAP_PX}px), sugere conteúdo curto "
                "demais pro espaço disponível (considere mais painéis ou mais texto)."
            ),
        }
        for r in results
        if r["maxGap"] > _MAX_VERTICAL_GAP_PX
    ]


async def check_deck(deck: DeckSpec) -> dict:
    """
    Roda os quatro checks e devolve um relatório agregado — nunca levanta
    exceção por causa de um PROBLEMA encontrado (isso é o ponto: são avisos,
    não falhas). Só propaga exceção se algo realmente quebrar na checagem em
    si (ex.: Chromium não sobe) — aí sim é uma falha real da etapa `qa`.
    """
    html = render_deck_html(deck)
    issues = [
        *_check_contrast(deck.theme),
        *_check_unresolved_assets(deck),
        *(await _check_overflow(html)),
        *(await _check_sparse_layout(html)),
    ]
    return {"issues": issues, "issue_count": len(issues)}
