"""Testes do renderizador HTML (app/decks/render.py)."""

import pytest

from app.decks.models import (
    BulletListBlock,
    DeckSpec,
    HeadingBlock,
    LayoutId,
    Panel,
    PanelListBlock,
    ParagraphBlock,
    QuoteBlock,
    Slide,
    SlideAsset,
    Theme,
)
from app.decks.render import render_deck_html


def _theme(**overrides) -> Theme:
    defaults = dict(
        palette={"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"},
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
        assert "--color-primary: #3B82F6;" in html
        assert "--color-background: #0D1B2E;" in html
        assert "--color-text: #F2F2F2;" in html
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


class TestAllLayoutsRender:
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


class TestInfographicLayout:
    """Layout adicionado em 2026-08-19 — eyebrow + subtítulo + ilustração + painéis + citação."""

    def _infographic_deck(self, **slide_kwargs) -> DeckSpec:
        defaults = dict(
            layout=LayoutId.INFOGRAPHIC,
            title="Migrar para System-Centric",
            eyebrow="Categoria | Subcategoria",
            citation="Teoria X — Fonte Y",
            body=[
                ParagraphBlock(text="Subtítulo de contexto."),
                PanelListBlock(
                    panels=[
                        Panel(heading="Painel 1", text="Texto do painel 1"),
                        Panel(heading="Painel 2", text="Texto do painel 2"),
                    ]
                ),
            ],
        )
        defaults.update(slide_kwargs)
        return DeckSpec(theme=_theme(), slides=[Slide(**defaults)])

    def test_eyebrow_renders_when_present(self):
        html = render_deck_html(self._infographic_deck())
        assert '<div class="slide__eyebrow">Categoria | Subcategoria</div>' in html

    def test_no_eyebrow_element_when_absent(self):
        # A regra CSS ".slide__eyebrow { ... }" sempre está no <style> —
        # o que importa é o elemento em si não aparecer no <body>.
        html = render_deck_html(self._infographic_deck(eyebrow=None))
        assert '<div class="slide__eyebrow">' not in html

    def test_citation_renders_when_present(self):
        html = render_deck_html(self._infographic_deck())
        assert '<div class="slide__citation">Teoria X — Fonte Y</div>' in html

    def test_no_citation_element_when_absent(self):
        html = render_deck_html(self._infographic_deck(citation=None))
        assert '<div class="slide__citation">' not in html

    def test_panels_render_as_panel_cards(self):
        html = render_deck_html(self._infographic_deck())
        # render_block gera as tags com aspas simples (mesmo estilo já usado
        # por bullets/blockquote nesse módulo — ver render.py).
        assert html.count("class='panel-card'") == 2
        assert "Painel 1" in html
        assert "Texto do painel 1" in html

    def test_default_illustration_position_puts_media_before_content_in_dom(self):
        # 2026-08-21: "left" é o padrão (illustration_position=None) — a
        # mídia vem ANTES do conteúdo no HTML (visualmente à esquerda), não
        # mais "no meio" da coluna de texto como era antes desta mudança.
        deck = self._infographic_deck(
            asset=SlideAsset(kind="image", ref="https://x.supabase.co/sign/foo.png"),
        )
        html = render_deck_html(deck)
        asset_pos = html.index('<img class="asset-image"')
        subtitle_pos = html.index("Subtítulo de contexto")
        panels_pos = html.index("class='panels-grid'")
        assert asset_pos < subtitle_pos < panels_pos

    def test_subtitle_still_comes_before_panels_when_no_asset(self):
        html = render_deck_html(self._infographic_deck())
        subtitle_pos = html.index("Subtítulo de contexto")
        panels_pos = html.index("class='panels-grid'")
        assert subtitle_pos < panels_pos


class TestIllustrationPosition:
    """Peça `illustration_position` (2026-08-21) — ver FEWSHOT_RULEBOOK.md."""

    def _deck_with_asset(self, **slide_kwargs) -> DeckSpec:
        defaults = dict(
            layout=LayoutId.INFOGRAPHIC,
            title="X",
            asset=SlideAsset(kind="image", ref="https://x.supabase.co/sign/foo.png"),
        )
        defaults.update(slide_kwargs)
        return DeckSpec(theme=_theme(), slides=[Slide(**defaults)])

    def test_left_is_the_default_and_puts_media_first_in_markup(self):
        html = render_deck_html(self._deck_with_asset())
        assert "illustration--left" in html
        media_pos = html.index('class="infographic__media"')
        content_pos = html.index('class="infographic__content"')
        assert media_pos < content_pos

    def test_right_sets_the_modifier_class(self):
        html = render_deck_html(self._deck_with_asset(illustration_position="right"))
        assert "illustration--right" in html

    def test_background_sets_the_modifier_class(self):
        html = render_deck_html(self._deck_with_asset(illustration_position="background"))
        assert "illustration--background" in html

    def test_none_omits_the_media_element_even_with_asset_present(self):
        html = render_deck_html(self._deck_with_asset(illustration_position="none"))
        assert "illustration--none" in html
        assert 'class="infographic__media"' not in html
        assert '<img class="asset-image"' not in html


class TestEyebrowStyle:
    def _deck(self, **slide_kwargs) -> DeckSpec:
        defaults = dict(layout=LayoutId.INFOGRAPHIC, title="X", eyebrow="Categoria")
        defaults.update(slide_kwargs)
        return DeckSpec(theme=_theme(), slides=[Slide(**defaults)])

    def test_pill_left_adds_modifier_class(self):
        html = render_deck_html(self._deck(eyebrow_style="pill-left"))
        assert 'class="slide__eyebrow slide__eyebrow--pill-left"' in html

    def test_none_style_hides_eyebrow_even_when_text_present(self):
        html = render_deck_html(self._deck(eyebrow_style="none"))
        assert '<div class="slide__eyebrow' not in html


class TestPanelAndArrangementStyle:
    def _deck(self, **slide_kwargs) -> DeckSpec:
        defaults = dict(
            layout=LayoutId.INFOGRAPHIC,
            title="X",
            body=[
                PanelListBlock(
                    panels=[Panel(heading="H1", text="T1"), Panel(heading="H2", text="T2")]
                )
            ],
        )
        defaults.update(slide_kwargs)
        return DeckSpec(theme=_theme(), slides=[Slide(**defaults)])

    def test_panel_style_adds_modifier_class_to_each_card(self):
        html = render_deck_html(self._deck(panel_style="void-frame"))
        assert html.count("class='panel-card panel-card--void-frame'") == 2

    def test_arrangement_adds_modifier_class_to_grid(self):
        html = render_deck_html(self._deck(arrangement="sequence-numbered"))
        assert "class='panels-grid panels-grid--sequence-numbered'" in html

    def test_no_style_or_arrangement_keeps_bare_classes(self):
        html = render_deck_html(self._deck())
        assert "class='panel-card'" in html
        assert "class='panels-grid'" in html


class TestCitationStyle:
    def _deck(self, **slide_kwargs) -> DeckSpec:
        defaults = dict(layout=LayoutId.INFOGRAPHIC, title="X", citation="Fonte X")
        defaults.update(slide_kwargs)
        return DeckSpec(theme=_theme(), slides=[Slide(**defaults)])

    def test_bordered_card_adds_modifier_class(self):
        html = render_deck_html(self._deck(citation_style="bordered-card"))
        assert 'class="slide__citation slide__citation--bordered-card"' in html

    def test_default_style_keeps_bare_class(self):
        html = render_deck_html(self._deck())
        assert '<div class="slide__citation">Fonte X</div>' in html


class TestMultipleSlides:
    def test_renders_one_section_per_slide_with_stable_ids(self):
        s1 = Slide(id="a", layout=LayoutId.COVER, title="Capa")
        s2 = Slide(id="b", layout=LayoutId.CLOSING, title="Fim")
        deck = DeckSpec(theme=_theme(), slides=[s1, s2])
        html = render_deck_html(deck)
        assert html.count('<section class="slide ') == 2
        assert 'id="a"' in html
        assert 'id="b"' in html
