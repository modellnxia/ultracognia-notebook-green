"""
Renderizador HTML — pega um `DeckSpec` já pronto e produz uma página HTML com
um `<section>` por slide, em 16:9, pronta pra ser impressa em PDF depois
(fatia 4, via Playwright).

Regra de ouro herdada da especificação original: **tema (cor/fonte/logo) vem
inteiramente do `DeckSpec`** — este módulo nunca decide nem improvisa nada
visual por conta própria, só interpreta o que já foi decidido antes dele no
pipeline (o agente que escolhe o tema é trabalho de fatia futura).

Nessa fatia (3), assets (`slide.asset`) ainda não são resolvidos pra imagem de
verdade — aparecem como um placeholder tracejado indicando o que iria ali.
Isso entra na fatia 6.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from app.decks.models import Block, DeckSpec

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_block(block: Block) -> Markup:
    """Converte um bloco de conteúdo tipado em HTML. Todo texto de usuário passa por escape()."""
    if block.type == "heading":
        tag = f"h{min(block.level + 1, 6)}"  # h2..h4 dentro do slide (h1 é o título do slide)
        return Markup(f"<{tag}>{escape(block.text)}</{tag}>")

    if block.type == "paragraph":
        return Markup(f"<p>{escape(block.text)}</p>")

    if block.type == "bullets":
        items = "".join(f"<li>{escape(item)}</li>" for item in block.items)
        return Markup(f"<ul class='bullets'>{items}</ul>")

    if block.type == "quote":
        cite = f"<cite>— {escape(block.attribution)}</cite>" if block.attribution else ""
        return Markup(f"<blockquote>{escape(block.text)}{cite}</blockquote>")

    if block.type == "panels":
        cards = "".join(
            f"<div class='panel-card'>"
            f"<div class='panel-card__heading'>{escape(p.heading)}</div>"
            f"<div class='panel-card__text'>{escape(p.text)}</div>"
            f"</div>"
            for p in block.panels
        )
        return Markup(f"<div class='panels-grid'>{cards}</div>")

    raise ValueError(f"Tipo de bloco desconhecido: {block.type!r}")


def _build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "jinja2"]),
    )
    env.globals["render_block"] = _render_block
    return env


_ENV = _build_environment()


def render_deck_html(deck: DeckSpec) -> str:
    """Renderiza o DeckSpec inteiro pra uma única página HTML (um <section> por slide)."""
    template = _ENV.get_template("deck.html.jinja2")
    return template.render(deck=deck)
