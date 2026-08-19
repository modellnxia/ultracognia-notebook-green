"""Testes do renderizador HTML (app/decks/render.py)."""

import pytest

from app.decks.models import (
    BulletListBlock,
    DeckSpec,
    HeadingBlock,
    LayoutId,
    ParagraphBlock,
    QuoteBlock,
    Slide,
    SlideAsset,
    Theme,
)
from app.decks.render import render_deck_html


def _theme(**overrides) -> Theme:
    defaults = dict(
        palette={"primary": "#123456", "background": "#FFFFFF", "text": "#111111"},
        font_stack="Inter, sans-serif",
    )
    defaults.update(overrides)
    return Theme(**defaults)


class TestThemeInjection:
    def test_palette_and_font_appear_as_css_custom_properties(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[Slide(layout=LayoutId.COVER, title="Título")],
        )
        html = render_deck_html(deck)
        assert "--color-primary: #123456;" in html
        assert "--color-background: #FFFFFF;" in html
        assert "--color-text: #111111;" in html
        assert "--font-stack: Inter, sans-serif;" in html

    def test_logo_rendered_when_present(self):
        deck = DeckSpec(
            theme=_theme(logo_url="https://exemplo.com/logo.png"),
            slides=[Slide(layout=LayoutId.COVER, title="X")],
        )
        html = render_deck_html(deck)
        assert 'src="https://exemplo.com/logo.png"' in html

    def test_no_logo_tag_when_absent(self):
        deck = DeckSpec(theme=_theme(), slides=[Slide(layout=LayoutId.COVER, title="X")])
        html = render_deck_html(deck)
        assert '<img class="slide__logo"' not in html


class TestAllSixLayoutsRender:
    @pytest.mark.parametrize("layout", list(LayoutId))
    def test_each_layout_produces_its_own_css_class(self, layout):
        deck = DeckSpec(
            theme=_theme(),
            slides=[Slide(layout=layout, title="Slide de teste")],
        )
        html = render_deck_html(deck)
        assert f'slide--{layout.value}' in html


class TestBlockRendering:
    def test_heading_paragraph_bullets_quote_all_render(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS,
                    title="Conteúdo",
                    body=[
                        HeadingBlock(text="Subtítulo", level=2),
                        ParagraphBlock(text="Um parágrafo qualquer."),
                        BulletListBlock(items=["Item 1", "Item 2"]),
                        QuoteBlock(text="Uma citação", attribution="Fulano"),
                    ],
                )
            ],
        )
        html = render_deck_html(deck)
        assert "<h3>Subtítulo</h3>" in html
        assert "<p>Um parágrafo qualquer.</p>" in html
        assert "<li>Item 1</li>" in html
        assert "<li>Item 2</li>" in html
        assert "<blockquote>Uma citação<cite>— Fulano</cite></blockquote>" in html

    def test_quote_without_attribution_omits_cite(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[Slide(layout=LayoutId.CLOSING, title="X", body=[QuoteBlock(text="Só a frase")])],
        )
        html = render_deck_html(deck)
        assert "<blockquote>Só a frase</blockquote>" in html
        assert "<cite>" not in html


class TestSecurityEscaping:
    def test_user_content_is_html_escaped(self):
        """Conteúdo malicioso/acidental não pode virar HTML/JS executável."""
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS,
                    title='<script>alert("xss")</script>',
                    body=[
                        ParagraphBlock(text="Preço: R$ 10 & desconto <especial>"),
                        BulletListBlock(items=['<img src=x onerror=alert(1)>']),
                    ],
                )
            ],
        )
        html = render_deck_html(deck)
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
        assert "<img src=x onerror" not in html
        assert "&amp;" in html  # o & do "R$ 10 & desconto" foi escapado


class TestTwoColumnSplit:
    def test_splits_body_roughly_in_half_across_two_columns(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TWO_COLUMN,
                    title="Comparação",
                    body=[
                        ParagraphBlock(text="Bloco 1"),
                        ParagraphBlock(text="Bloco 2"),
                        ParagraphBlock(text="Bloco 3"),
                    ],
                )
            ],
        )
        html = render_deck_html(deck)
        assert html.count('<div class="two-column__col">') == 2
        # 3 blocos: 2 na primeira coluna, 1 na segunda (round up pra primeira)
        first_col_start = html.index('<div class="two-column__col">')
        second_col_start = html.index('<div class="two-column__col">', first_col_start + 1)
        assert "Bloco 1" in html[:second_col_start]
        assert "Bloco 2" in html[:second_col_start]
        assert "Bloco 3" in html[second_col_start:]


class TestAssetPlaceholder:
    def test_asset_renders_as_placeholder_with_kind_and_ref(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.DIAGRAM_FULL,
                    title="Arquitetura",
                    asset=SlideAsset(kind="diagram", ref="fluxo do pipeline"),
                )
            ],
        )
        html = render_deck_html(deck)
        assert "asset-placeholder" in html
        assert "diagram" in html
        assert "fluxo do pipeline" in html

    def test_asset_renders_as_img_when_ref_is_a_resolved_url(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.DIAGRAM_FULL,
                    title="Arquitetura",
                    asset=SlideAsset(
                        kind="image",
                        ref="https://proj.supabase.co/storage/v1/object/sign/decks/x/y.png?token=abc",
                    ),
                )
            ],
        )
        html = render_deck_html(deck)
        # A regra CSS ".asset-placeholder { ... }" sempre está no <style> —
        # o que importa é que o elemento em si não apareça no <body>.
        assert 'class="asset-placeholder"' not in html
        assert '<img class="asset-image" src="https://proj.supabase.co/storage/v1/object/sign/decks/x/y.png?token=abc"' in html


class TestMultipleSlides:
    def test_renders_one_section_per_slide_with_stable_ids(self):
        s1 = Slide(id="a", layout=LayoutId.COVER, title="Capa")
        s2 = Slide(id="b", layout=LayoutId.CLOSING, title="Fim")
        deck = DeckSpec(theme=_theme(), slides=[s1, s2])
        html = render_deck_html(deck)
        assert html.count('<section class="slide ') == 2
        assert 'id="a"' in html
        assert 'id="b"' in html
