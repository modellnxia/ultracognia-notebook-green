"""
Renderizador PPTX — mesmo `DeckSpec` que vira PDF (render.py/pdf.py) também
vira um .pptx de verdade, editável no PowerPoint/Google Slides/Keynote.

Não é um "PDF convertido" — é construído direto do `DeckSpec` com
`python-pptx` (biblioteca pura Python, sem dependência de serviço externo,
mesma filosofia de portabilidade do resto do módulo). Os dois renderizadores
leem o mesmo documento e aplicam as mesmas regras de layout/tema
independentemente um do outro — igual à foto do slide em HTML, nenhum dos
dois "adivinha" nada que não esteja no `DeckSpec`.

Assets (imagem/diagrama) precisam ser baixados aqui (`python-pptx` exige os
bytes da imagem, não aceita URL) — por isso `render_deck_pptx` é async e
espera receber o deck já com `asset.ref` resolvido pra uma URL assinada
(mesma transformação usada antes de gerar o HTML, ver
`steps.py::_with_resolved_asset_urls`).
"""

import io
import logging

import httpx
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.decks.models import Block, DeckSpec, LayoutId, Panel, Slide, TableBlock

logger = logging.getLogger(__name__)

# 1280x720 (16:9), mesma proporção do PDF (ver templates/deck.html.jinja2) —
# em polegadas, a 96 DPI de referência.
_SLIDE_WIDTH = Inches(13.333)
_SLIDE_HEIGHT = Inches(7.5)

_MARGIN = Inches(0.55)
_FULLSCREEN_LAYOUTS = {LayoutId.COVER, LayoutId.SECTION_BREAK, LayoutId.CLOSING}

# ── Coordenadas do layout `infographic` (2026-08-19; peças de composição
# desde 2026-08-21 — ver FEWSHOT_RULEBOOK.md) — eyebrow + título + subtítulo
# + ilustração hero + painéis + citação, mesmo espírito do CSS em
# templates/deck.html.jinja2, adaptado pra coordenadas fixas em polegadas
# (python-pptx não tem flexbox).
_INFO_EYEBROW_TOP = Inches(0.35)
_INFO_TITLE_TOP = Inches(0.68)
_INFO_SUBTITLE_TOP = Inches(1.28)
_INFO_ASSET_TOP = Inches(1.85)
_INFO_ASSET_HEIGHT = Inches(3.05)
_INFO_PANELS_TOP = Inches(5.05)
_INFO_PANELS_HEIGHT = Inches(1.5)
_INFO_CITATION_TOP = Inches(6.7)

# peça: illustration_position — "left"/"right" dividem a área abaixo do
# título em coluna de mídia (~42%) + coluna de conteúdo, mesma proporção do
# CSS (`.infographic__media { flex: 0 0 42% }`).
_INFO_SPLIT_TOP = Inches(1.28)
_INFO_SPLIT_BOTTOM = Inches(7.05)
_INFO_MEDIA_WIDTH_FRACTION = 0.42
_INFO_COLUMN_GAP = Inches(0.3)


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _set_background(slide, hex_color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(hex_color)


def _set_fill_alpha(fill, alpha_fraction: float) -> None:
    """
    python-pptx (1.0.2 aqui) não tem API de alto nível pra transparência de
    preenchimento — mesma situação de `_style_bullet` (marcador de lista),
    mexe direto no XML: injeta `<a:alpha>` dentro do `<a:srgbClr>` do fill.
    `alpha_fraction` é opacidade (1.0 = opaco, 0.0 = invisível).
    """
    srgb_clr = fill.fore_color._xFill.find(qn("a:srgbClr"))
    alpha = srgb_clr.makeelement(qn("a:alpha"), {"val": str(int(alpha_fraction * 100_000))})
    srgb_clr.append(alpha)


def _add_textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(left, top, width, height)
    box.text_frame.word_wrap = True
    return box.text_frame


def _style_bullet(paragraph, color_hex: str) -> None:
    """python-pptx não tem API de alto nível pra marcador de lista — mexe no XML."""
    pPr = paragraph._pPr
    if pPr is None:
        pPr = paragraph._p.get_or_add_pPr()
    buFont = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
    buChar = pPr.makeelement(qn("a:buChar"), {"char": "•"})
    pPr.append(buFont)
    pPr.append(buChar)


def _write_block(text_frame, block: Block, *, base_size: int, color_hex: str, first: bool) -> None:
    para = text_frame.paragraphs[0] if first else text_frame.add_paragraph()

    if block.type == "heading":
        run = para.add_run()
        run.text = block.text
        run.font.size = Pt(base_size + (3 - block.level) * 2)
        run.font.bold = True
        run.font.color.rgb = _rgb(color_hex)
        para.space_before = Pt(6)

    elif block.type == "paragraph":
        run = para.add_run()
        run.text = block.text
        run.font.size = Pt(base_size)
        run.font.color.rgb = _rgb(color_hex)
        para.space_after = Pt(8)

    elif block.type == "bullets":
        for i, item in enumerate(block.items):
            p = para if i == 0 else text_frame.add_paragraph()
            run = p.add_run()
            run.text = item
            run.font.size = Pt(base_size)
            run.font.color.rgb = _rgb(color_hex)
            p.level = 0
            _style_bullet(p, color_hex)
            p.space_after = Pt(6)

    elif block.type == "quote":
        run = para.add_run()
        run.text = block.text
        run.font.size = Pt(base_size + 2)
        run.font.italic = True
        run.font.color.rgb = _rgb(color_hex)
        if block.attribution:
            attr_p = text_frame.add_paragraph()
            attr_run = attr_p.add_run()
            attr_run.text = f"— {block.attribution}"
            attr_run.font.size = Pt(base_size - 4)
            attr_run.font.color.rgb = _rgb(color_hex)

    else:
        logger.warning("Tipo de bloco desconhecido no render PPTX: %r — ignorado.", block.type)


def _add_table(
    slide, block: TableBlock, *, left, top, width, height, background_hex: str, accent_hex: str, fg_hex: str
) -> None:
    """
    Tabela nativa do PowerPoint (2026-08-24, Fatia B) — `python-pptx::add_table`
    já suportava isso, nunca tínhamos usado (achado comparando com PptxGenJS,
    que também tem tabela nativa — mas é biblioteca JS, o que importava era a
    capacidade em si, já disponível na nossa dependência atual). Diferente de
    `slide.shapes.add_picture`, isso fica editável de verdade no PowerPoint —
    não é imagem de tabela.

    Cabeçalho com fundo de destaque (accent) pra se diferenciar das linhas de
    dado, que herdam o fundo do tema — evita brigar com paletas variadas
    (a IA escolhe livremente a cada deck) sem precisar de uma cor de
    "superfície" nova no `Theme`.
    """
    n_rows = len(block.rows) + 1  # +1 pro cabeçalho
    n_cols = len(block.headers)
    graphic_frame = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = graphic_frame.table

    for col_idx, header_text in enumerate(block.headers):
        cell = table.cell(0, col_idx)
        cell.text = header_text
        cell.margin_left = cell.margin_right = Inches(0.08)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(accent_hex)
        run = cell.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.color.rgb = _rgb(background_hex)

    for row_idx, row in enumerate(block.rows, start=1):
        for col_idx, cell_text in enumerate(row):
            cell = table.cell(row_idx, col_idx)
            cell.text = cell_text
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(background_hex)
            run = cell.text_frame.paragraphs[0].runs[0]
            run.font.size = Pt(10.5)
            run.font.color.rgb = _rgb(fg_hex)


async def _fetch_image_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.warning("Falha ao baixar asset pro PPTX (%s) — slide fica sem imagem: %s", url, exc)
        return None


def _add_title(slide, text: str, *, color_hex: str, size: int, bold: bool = True, center: bool = False) -> None:
    tf = _add_textbox(slide, _MARGIN, Inches(0.45), _SLIDE_WIDTH - 2 * _MARGIN, Inches(1.3))
    p = tf.paragraphs[0]
    if center:
        p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color_hex)


def _add_eyebrow(
    slide, text: str, *, color_hex: str, style: str | None, left: int, width: int, center: bool = False
) -> None:
    """peça: eyebrow_style — python-pptx não tem clip-path/box-shadow; a única
    diferença que dá pra replicar com fidelidade razoável é o alinhamento
    (pill-center centraliza, o resto fica à esquerda como sempre)."""
    tf = _add_textbox(slide, left, _INFO_EYEBROW_TOP, width, Inches(0.3))
    p = tf.paragraphs[0]
    if center or style == "pill-center":
        p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text.upper()
    run.font.size = Pt(11)
    run.font.bold = True
    run.font.color.rgb = _rgb(color_hex)


def _add_citation(
    slide, text: str, *, color_hex: str, accent_hex: str, style: str | None, left: int, width: int
) -> None:
    """peça: citation_style — bordered-card ganha uma moldura fina (mesmo
    espírito do `border` do CSS); split-two ganha uma barra de destaque à
    esquerda em vez de linha no topo. bar-circuit-corners (padrão) fica sem
    moldura, igual sempre foi."""
    top = _INFO_CITATION_TOP
    height = Inches(0.35)
    if style == "bordered-card":
        box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        box.fill.background()
        box.line.color.rgb = _rgb(accent_hex)
        box.line.width = Pt(0.75)
        box.shadow.inherit = False
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left, tf.margin_right = Inches(0.12), Inches(0.12)
    elif style == "split-two":
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, Inches(0.035), height)
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(accent_hex)
        bar.line.fill.background()
        bar.shadow.inherit = False
        tf = _add_textbox(slide, left + Inches(0.14), top, width - Inches(0.14), height)
    else:
        tf = _add_textbox(slide, left, top, width, height)

    run = tf.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(9.5)
    run.font.italic = True
    run.font.color.rgb = _rgb(color_hex)


_SEQUENCE_LETTERS = "ABCDEFGH"


def _add_panels(
    slide,
    panels: list[Panel],
    *,
    color_hex: str,
    accent_hex: str,
    left: int,
    width: int,
    panel_style: str | None,
    arrangement: str | None,
) -> None:
    """
    Cada painel vira uma barrinha de destaque (mesmo espírito do
    `border-left: accent` do HTML) + uma caixa de texto com título curto em
    negrito + descrição — sem fundo de card (python-pptx não tem
    transparência de preenchimento numa API de alto nível, e um fundo sólido
    arbitrário sem paleta de "superfície" no `Theme` arriscava destoar do
    tema). Prioriza ficar editável e correto sobre replicar 100% do visual.

    peça: panel_style — `void-frame` troca a barra por uma moldura fina ao
    redor do painel inteiro; `borderless-icon` não desenha moldura nenhuma.
    peça: arrangement — `stacked` empilha verticalmente em vez de lado a
    lado; `sequence-numbered`/`sequence-lettered` prefixam o heading com o
    marcador (python-pptx não tem `counter()` do CSS, então o número/letra
    vira parte do texto mesmo); `comparison-columns` é visualmente igual ao
    padrão em coluna (row) aqui — a linha divisória do CSS não tem
    equivalente barato em python-pptx, aceito como diferença conhecida.
    """
    n = len(panels)
    gap = Inches(0.18)
    stacked = arrangement == "stacked"

    if stacked:
        item_height = (_INFO_PANELS_HEIGHT - gap * (n - 1)) / n
    else:
        item_width = (width - gap * (n - 1)) / n

    for i, panel in enumerate(panels):
        if stacked:
            item_left, item_top = left, _INFO_PANELS_TOP + i * (item_height + gap)
            item_w, item_h = width, item_height
        else:
            item_left, item_top = left + i * (item_width + gap), _INFO_PANELS_TOP
            item_w, item_h = item_width, _INFO_PANELS_HEIGHT

        text_left = item_left
        if panel_style == "void-frame":
            box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, item_left, item_top, item_w, item_h)
            box.fill.background()
            box.line.color.rgb = _rgb(accent_hex)
            box.line.width = Pt(0.75)
            box.shadow.inherit = False
            text_left = item_left + Inches(0.08)
        elif panel_style != "borderless-icon":
            bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, item_left, item_top, Inches(0.035), item_h)
            bar.fill.solid()
            bar.fill.fore_color.rgb = _rgb(accent_hex)
            bar.line.fill.background()
            bar.shadow.inherit = False
            text_left = item_left + Inches(0.12)

        tf = _add_textbox(slide, text_left, item_top, item_w - (text_left - item_left), item_h)
        heading_text = panel.heading
        if arrangement == "sequence-numbered":
            heading_text = f"{i + 1}. {heading_text}"
        elif arrangement == "sequence-lettered":
            letter = _SEQUENCE_LETTERS[i] if i < len(_SEQUENCE_LETTERS) else str(i + 1)
            heading_text = f"{letter}. {heading_text}"

        heading_run = tf.paragraphs[0].add_run()
        heading_run.text = heading_text
        heading_run.font.size = Pt(12)
        heading_run.font.bold = True
        heading_run.font.color.rgb = _rgb(accent_hex)

        text_p = tf.add_paragraph()
        text_run = text_p.add_run()
        text_run.text = panel.text
        text_run.font.size = Pt(10.5)
        text_run.font.color.rgb = _rgb(color_hex)


async def _add_asset_at(slide, asset, *, left, top, width, height, fg: str, cover: bool = False) -> None:
    image_bytes = await _fetch_image_bytes(asset.ref) if asset.ref.startswith("http") else None
    if image_bytes:
        pic = slide.shapes.add_picture(io.BytesIO(image_bytes), left, top, width=width, height=height)
        if cover:
            # imagem de fundo (illustration_position="background") deve ficar
            # atrás de todo o resto — manda pro fundo da pilha de shapes.
            slide.shapes._spTree.remove(pic._element)
            slide.shapes._spTree.insert(2, pic._element)
    else:
        ph_tf = _add_textbox(slide, left, top, width, height)
        p = ph_tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = f"[{asset.kind}: {asset.ref}]"
        run.font.size = Pt(12)
        run.font.italic = True
        run.font.color.rgb = _rgb(fg)


async def _render_infographic_slide(slide, slide_spec: Slide, fg: str, accent: str, background_hex: str) -> None:
    """
    peça: illustration_position (2026-08-21) — "left"/"right" dividem a área
    abaixo do título em coluna de mídia + coluna de conteúdo (subtítulo,
    painéis, citação ficam só na coluna de conteúdo, mesma proporção do CSS);
    "background" espalha a imagem pelo slide inteiro, atrás de tudo, com o
    conteúdo centralizado por cima; "none"/sem asset usa a largura inteira
    pro conteúdo, como sempre foi.
    """
    position = slide_spec.illustration_position or "left"
    has_media = slide_spec.asset is not None and position != "none"
    full_width = _SLIDE_WIDTH - 2 * _MARGIN

    if has_media and position in ("left", "right"):
        media_width = int(full_width * _INFO_MEDIA_WIDTH_FRACTION)
        content_width = full_width - media_width - _INFO_COLUMN_GAP
        if position == "left":
            media_left = _MARGIN
            content_left = _MARGIN + media_width + _INFO_COLUMN_GAP
        else:
            content_left = _MARGIN
            media_left = _MARGIN + content_width + _INFO_COLUMN_GAP
    else:
        media_left, media_width = _MARGIN, full_width
        content_left, content_width = _MARGIN, full_width

    if has_media and position == "background":
        await _add_asset_at(
            slide, slide_spec.asset, left=0, top=0, width=_SLIDE_WIDTH, height=_SLIDE_HEIGHT, fg=fg, cover=True
        )
        scrim = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, _SLIDE_WIDTH, _SLIDE_HEIGHT)
        scrim.fill.solid()
        scrim.fill.fore_color.rgb = _rgb(background_hex)
        _set_fill_alpha(scrim.fill, 0.55)  # opacidade 55% — deixa a ilustração de fundo entrever
        scrim.line.fill.background()
        scrim.shadow.inherit = False

    if slide_spec.eyebrow and slide_spec.eyebrow_style != "none":
        _add_eyebrow(
            slide, slide_spec.eyebrow, color_hex=accent, style=slide_spec.eyebrow_style,
            left=content_left, width=content_width, center=(position == "background"),
        )

    title_tf = _add_textbox(slide, _MARGIN, _INFO_TITLE_TOP, full_width, Inches(0.55))
    title_p = title_tf.paragraphs[0]
    if position == "background":
        title_p.alignment = PP_ALIGN.CENTER
    title_run = title_p.add_run()
    title_run.text = slide_spec.title
    title_run.font.size = Pt(24)
    title_run.font.bold = True
    title_run.font.color.rgb = _rgb(fg)

    subtitle_blocks = [b for b in slide_spec.body if b.type != "panels"]
    panel_blocks = [b for b in slide_spec.body if b.type == "panels"]

    if subtitle_blocks:
        sub_tf = _add_textbox(slide, content_left, _INFO_SUBTITLE_TOP, content_width, Inches(0.5))
        for j, block in enumerate(subtitle_blocks):
            _write_block(sub_tf, block, base_size=13, color_hex=fg, first=(j == 0))

    if has_media and position in ("left", "right"):
        await _add_asset_at(
            slide, slide_spec.asset,
            left=media_left, top=_INFO_SPLIT_TOP, width=media_width, height=_INFO_SPLIT_BOTTOM - _INFO_SPLIT_TOP,
            fg=fg,
        )

    for block in panel_blocks:
        _add_panels(
            slide, block.panels, color_hex=fg, accent_hex=accent,
            left=content_left, width=content_width,
            panel_style=slide_spec.panel_style, arrangement=slide_spec.arrangement,
        )

    if slide_spec.citation:
        _add_citation(
            slide, slide_spec.citation, color_hex=fg, accent_hex=accent,
            style=slide_spec.citation_style, left=content_left, width=content_width,
        )


async def _render_slide(prs: Presentation, deck: DeckSpec, slide_spec: Slide) -> None:
    theme = deck.theme
    blank_layout = prs.slide_layouts[6]  # layout em branco — controlamos tudo manualmente
    slide = prs.slides.add_slide(blank_layout)

    fullscreen = slide_spec.layout in _FULLSCREEN_LAYOUTS
    if fullscreen:
        _set_background(slide, theme.palette["primary"])
        fg = theme.palette["background"]  # CSS inverte: fundo do tema vira cor do texto aqui
    else:
        _set_background(slide, theme.palette["background"])
        fg = theme.palette["text"]
    accent = theme.palette.get("accent", theme.palette["primary"])

    if slide_spec.layout == LayoutId.INFOGRAPHIC:
        await _render_infographic_slide(slide, slide_spec, fg, accent, theme.palette["background"])
        return

    if slide_spec.layout in (LayoutId.COVER, LayoutId.CLOSING):
        _add_title(slide, slide_spec.title, color_hex=fg, size=40, center=True)
    elif slide_spec.layout == LayoutId.SECTION_BREAK:
        _add_title(slide, slide_spec.title.upper(), color_hex=fg, size=32, center=True)
    else:
        _add_title(slide, slide_spec.title, color_hex=fg, size=28)

    if slide_spec.layout == LayoutId.TWO_COLUMN and slide_spec.body:
        half = (len(slide_spec.body) + 1) // 2
        col_width = (_SLIDE_WIDTH - 3 * _MARGIN) / 2
        for i, blocks in enumerate((slide_spec.body[:half], slide_spec.body[half:])):
            if not blocks:
                continue
            left = _MARGIN + i * (col_width + _MARGIN)
            tf = _add_textbox(slide, left, Inches(1.9), col_width, _SLIDE_HEIGHT - Inches(2.3))
            for j, block in enumerate(blocks):
                _write_block(tf, block, base_size=16, color_hex=fg, first=(j == 0))

    elif slide_spec.layout not in _FULLSCREEN_LAYOUTS and slide_spec.body:
        # TableBlock (2026-08-24, Fatia B) não cabe dentro do text_frame corrido
        # que `_write_block` escreve (tabela é uma shape própria, não texto) —
        # separado do resto, escrito abaixo do texto normal. Escopo desta
        # fatia: só este branch genérico (title-bullets/diagram-full/
        # section-break com corpo); em two-column e infographic uma
        # TableBlock cai no `else` de `_write_block` (log + ignorado) —
        # limitação conhecida, documentada, não implementada ainda.
        prose_blocks = [b for b in slide_spec.body if b.type != "table"]
        table_blocks = [b for b in slide_spec.body if b.type == "table"]

        body_height = _SLIDE_HEIGHT - Inches(2.3)
        if slide_spec.layout == LayoutId.DIAGRAM_FULL and slide_spec.asset:
            body_height = Inches(1.4)  # deixa espaço pra imagem abaixo
        elif table_blocks:
            # reserva espaço pra tabela abaixo do texto, proporcional ao total
            # de linhas (cabeçalho + dados) — mesmo raciocínio do espaço
            # reservado pra imagem em DIAGRAM_FULL, acima.
            total_table_rows = sum(len(b.rows) + 1 for b in table_blocks)
            body_height = max(Inches(0.8), body_height - Inches(0.32) * total_table_rows)

        if prose_blocks:
            tf = _add_textbox(slide, _MARGIN, Inches(1.9), _SLIDE_WIDTH - 2 * _MARGIN, body_height)
            for j, block in enumerate(prose_blocks):
                _write_block(tf, block, base_size=18, color_hex=fg, first=(j == 0))

        table_top = Inches(1.9) + body_height + Inches(0.15) if prose_blocks else Inches(1.9)
        for table_block in table_blocks:
            table_height = Inches(0.32) * (len(table_block.rows) + 1)
            _add_table(
                slide, table_block,
                left=_MARGIN, top=table_top, width=_SLIDE_WIDTH - 2 * _MARGIN, height=table_height,
                background_hex=theme.palette["background"], accent_hex=accent, fg_hex=fg,
            )
            table_top += table_height + Inches(0.15)

    if slide_spec.layout == LayoutId.DIAGRAM_FULL and slide_spec.asset:
        asset = slide_spec.asset
        if asset.ref.startswith("http"):
            image_bytes = await _fetch_image_bytes(asset.ref)
            if image_bytes:
                img_top = Inches(3.4) if slide_spec.body else Inches(1.9)
                img_height = _SLIDE_HEIGHT - img_top - _MARGIN
                slide.shapes.add_picture(
                    io.BytesIO(image_bytes), _MARGIN, img_top,
                    width=_SLIDE_WIDTH - 2 * _MARGIN, height=img_height,
                )
                return
        # não resolvido (ou falhou o download) — mesmo placeholder textual do HTML
        placeholder_top = Inches(3.4) if slide_spec.body else Inches(1.9)
        tf = _add_textbox(
            slide, _MARGIN, placeholder_top,
            _SLIDE_WIDTH - 2 * _MARGIN, _SLIDE_HEIGHT - placeholder_top - _MARGIN,
        )
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = f"[{asset.kind}: {asset.ref}]"
        run.font.size = Pt(14)
        run.font.italic = True
        run.font.color.rgb = _rgb(fg)


async def render_deck_pptx(deck: DeckSpec) -> bytes:
    """Constrói o .pptx inteiro a partir do DeckSpec e devolve os bytes prontos pra upload."""
    prs = Presentation()
    prs.slide_width = _SLIDE_WIDTH
    prs.slide_height = _SLIDE_HEIGHT

    for slide_spec in deck.slides:
        await _render_slide(prs, deck, slide_spec)

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
