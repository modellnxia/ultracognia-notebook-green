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
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.decks.models import Block, DeckSpec, LayoutId, Slide

logger = logging.getLogger(__name__)

# 1280x720 (16:9), mesma proporção do PDF (ver templates/deck.html.jinja2) —
# em polegadas, a 96 DPI de referência.
_SLIDE_WIDTH = Inches(13.333)
_SLIDE_HEIGHT = Inches(7.5)

_MARGIN = Inches(0.55)
_FULLSCREEN_LAYOUTS = {LayoutId.COVER, LayoutId.SECTION_BREAK, LayoutId.CLOSING}


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _set_background(slide, hex_color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(hex_color)


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
        body_height = _SLIDE_HEIGHT - Inches(2.3)
        if slide_spec.layout == LayoutId.DIAGRAM_FULL and slide_spec.asset:
            body_height = Inches(1.4)  # deixa espaço pra imagem abaixo
        tf = _add_textbox(slide, _MARGIN, Inches(1.9), _SLIDE_WIDTH - 2 * _MARGIN, body_height)
        for j, block in enumerate(slide_spec.body):
            _write_block(tf, block, base_size=18, color_hex=fg, first=(j == 0))

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
