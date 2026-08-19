"""
Impressão do HTML do deck em PDF, via Playwright — mesma técnica usada pro
relatório técnico semanal (headless Chromium, `page.pdf()`).

Cada slide já nasce como uma caixa de 1280×720 (16:9) com
`page-break-after: always` (ver templates/deck.html.jinja2) — isso faz cada
slide virar exatamente uma página do PDF, sem margem entre elas.
"""

from playwright.async_api import async_playwright


async def render_deck_pdf(html: str) -> bytes:
    """Recebe o HTML já renderizado (ver render.py) e devolve os bytes do PDF."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            return await page.pdf(
                width="1280px",
                height="720px",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            await browser.close()
