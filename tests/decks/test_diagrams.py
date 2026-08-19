"""
Teste do renderizador de diagramas (app/decks/diagrams.py) — Chromium real
via Playwright + o mermaid.min.js vendorizado de verdade, não mockado.
Mesma lógica do test_pdf.py: renderizar Mermaid só se prova rodando de
verdade — um mock não diria nada sobre se a sintaxe embutida no HTML
realmente vira um SVG.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.decks import diagrams
from app.decks.diagrams import DiagramRenderError, render_diagram_png


class TestRenderDiagramPng:
    @pytest.mark.asyncio
    async def test_produces_valid_png_for_valid_flowchart(self):
        png_bytes = await render_diagram_png("flowchart LR\nA[Início] --> B[Meio] --> C[Fim]")

        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(png_bytes) > 500  # PNG vazio/quebrado seria bem menor que isso

    @pytest.mark.asyncio
    async def test_produces_valid_png_for_sequence_diagram(self):
        png_bytes = await render_diagram_png(
            "sequenceDiagram\nparticipant A\nparticipant B\nA->>B: mensagem"
        )

        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_gracefully_renders_error_graphic_for_invalid_syntax(self):
        """
        Descoberta ao testar de verdade: o Mermaid (v10) nunca "falha" pra
        sintaxe malformada — ele sempre renderiza um SVG de erro em vez de
        deixar de criar o elemento. Ou seja, render_diagram_png() não levanta
        DiagramRenderError nesse caso — ele devolve um PNG válido (só que da
        figura de erro do próprio Mermaid). O caminho de exceção real é outro
        (falha do Playwright/Chromium em si) — ver teste abaixo, mockado.
        """
        png_bytes = await render_diagram_png("isso não é sintaxe mermaid nenhuma { } [[[")

        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_raises_diagram_render_error_when_element_never_appears(self):
        """Caminho de erro real: o elemento SVG nunca aparece (ex.: Chromium travou, JS quebrou)."""
        fake_page = AsyncMock()
        fake_page.set_content = AsyncMock()
        fake_page.wait_for_selector = AsyncMock(side_effect=TimeoutError("timeout"))
        fake_browser = AsyncMock()
        fake_browser.new_page = AsyncMock(return_value=fake_page)
        fake_chromium = AsyncMock()
        fake_chromium.launch = AsyncMock(return_value=fake_browser)
        fake_playwright_ctx = AsyncMock()
        fake_playwright_ctx.chromium = fake_chromium
        fake_playwright_cm = AsyncMock()
        fake_playwright_cm.__aenter__ = AsyncMock(return_value=fake_playwright_ctx)
        fake_playwright_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.decks.diagrams.async_playwright", return_value=fake_playwright_cm):
            with pytest.raises(DiagramRenderError):
                await diagrams.render_diagram_png("flowchart LR\nA-->B")
