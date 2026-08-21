"""
Teste do PDF (app/decks/pdf.py) — Chromium real via Playwright, não mockado.
Renderizar HTML pra PDF é exatamente o tipo de coisa que só se prova rodando
de verdade; um mock aqui não provaria nada sobre o resultado real.
"""

import pytest

from app.decks.models import DeckSpec, LayoutId, Slide, Theme
from app.decks.pdf import render_deck_pdf
from app.decks.render import render_deck_html


def _sample_deck(n_slides: int) -> DeckSpec:
    theme = Theme(
        palette={"primary": "#123456", "background": "#0D1B2E", "text": "#F2F2F2"},
        font_stack="Arial, sans-serif",
    )
    slides = [Slide(layout=LayoutId.COVER, title=f"Slide {i}") for i in range(n_slides)]
    return DeckSpec(theme=theme, slides=slides)


class TestRenderDeckPdf:
    @pytest.mark.asyncio
    async def test_produces_valid_pdf_bytes(self):
        html = render_deck_html(_sample_deck(1))
        pdf_bytes = await render_deck_pdf(html)

        assert pdf_bytes.startswith(b"%PDF-")
        assert len(pdf_bytes) > 1000  # PDF vazio/quebrado seria bem menor que isso

    @pytest.mark.asyncio
    async def test_page_count_matches_slide_count(self):
        html = render_deck_html(_sample_deck(3))
        pdf_bytes = await render_deck_pdf(html)

        # Contagem crua de páginas via o marcador padrão do formato PDF —
        # suficiente aqui sem trazer uma lib de parsing de PDF só pra isso.
        # "/Type /Page" é prefixo de "/Type /Pages" (o nó pai da árvore),
        # então subtrai esse antes de comparar.
        page_markers = pdf_bytes.count(b"/Type/Page") + pdf_bytes.count(b"/Type /Page")
        pages_markers = pdf_bytes.count(b"/Type/Pages") + pdf_bytes.count(b"/Type /Pages")
        assert page_markers - pages_markers == 3
