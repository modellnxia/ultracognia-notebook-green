"""
Renderizador de diagramas Mermaid — via Playwright (mesmo Chromium headless
já usado em `pdf.py`), sem depender de nenhuma API externa nem de CDN em
tempo de execução: o `mermaid.min.js` fica vendorizado dentro do próprio
módulo (`static/mermaid.min.js`, versão fixa 10.9.1), embutido inline no
HTML que o Chromium carrega — mantém a promessa de portabilidade ("copiar a
pasta e sair funcionando", ver README.md).

Diferente de `pdf.py` (que imprime a página inteira), aqui só o elemento SVG
do diagrama é capturado — o resultado é um PNG do diagrama isolado, pronto
pra subir no Storage e entrar num slide como qualquer outra imagem.
"""

import logging
from functools import lru_cache
from pathlib import Path

from markupsafe import escape
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_MERMAID_JS_PATH = Path(__file__).parent / "static" / "mermaid.min.js"


class DiagramRenderError(RuntimeError):
    """A sintaxe Mermaid é inválida ou o Chromium não conseguiu renderizar o diagrama."""


@lru_cache(maxsize=1)
def _mermaid_js_source() -> str:
    if not _MERMAID_JS_PATH.exists():
        raise DiagramRenderError(f"mermaid.min.js não encontrado em {_MERMAID_JS_PATH}.")
    return _MERMAID_JS_PATH.read_text(encoding="utf-8")


def _build_html(mermaid_code: str) -> str:
    # escape() protege contra o editorial/LLM injetando HTML por engano no
    # meio do código do diagrama — o Mermaid decodifica de volta pra texto.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script>{_mermaid_js_source()}</script>
</head>
<body style="margin:0;background:#ffffff;">
<pre class="mermaid">{escape(mermaid_code)}</pre>
<script>mermaid.initialize({{ startOnLoad: true }});</script>
</body></html>"""


async def render_diagram_png(mermaid_code: str) -> bytes:
    """
    Renderiza um diagrama Mermaid e devolve PNG (só o diagrama, fundo branco,
    tamanho natural do SVG gerado).

    Nota (descoberta testando de verdade): o Mermaid v10 não "falha" pra
    sintaxe malformada — ele sempre renderiza um SVG de erro em vez de não
    criar elemento nenhum. Ou seja, sintaxe ruim do LLM não levanta
    `DiagramRenderError` aqui, só produz um PNG feio (a própria figura de
    erro do Mermaid) em vez de travar o job — na prática, mais uma camada de
    resiliência de graça. `DiagramRenderError` cobre falhas reais de
    infraestrutura (Chromium não sobe, elemento nunca aparece por outro
    motivo).
    """
    html = _build_html(mermaid_code)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            try:
                await page.wait_for_selector(".mermaid svg", timeout=10_000)
            except Exception as exc:
                raise DiagramRenderError(
                    f"Diagrama não renderizou (sintaxe Mermaid inválida?): {exc}"
                ) from exc
            element = await page.query_selector(".mermaid svg")
            return await element.screenshot()
        finally:
            await browser.close()
