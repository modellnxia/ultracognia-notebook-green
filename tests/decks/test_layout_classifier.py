"""
Testes de app/decks/layout_classifier.py — funções puras, determinísticas,
sem IA e sem Chromium (mesmo raciocínio de test_models.py: rápido, sem mock
nenhum necessário).
"""

from app.decks.layout_classifier import CLASSIFIABLE_LAYOUTS, classify, slide_signals, suggest_layout
from app.decks.models import (
    BulletListBlock,
    LayoutId,
    Panel,
    PanelListBlock,
    ParagraphBlock,
    Slide,
    SlideAsset,
)


class TestSuggestLayout:
    def test_diagram_asset_suggests_diagram_full(self):
        suggestion = suggest_layout(has_diagram=True, has_image=False, has_panels=False, body_block_count=1)
        assert suggestion == LayoutId.DIAGRAM_FULL

    def test_diagram_wins_even_with_panels(self):
        """Diagrama é o sinal mais forte/específico — vence painéis se os dois aparecerem juntos."""
        suggestion = suggest_layout(has_diagram=True, has_image=False, has_panels=True, body_block_count=3)
        assert suggestion == LayoutId.DIAGRAM_FULL

    def test_panels_suggest_infographic(self):
        suggestion = suggest_layout(has_diagram=False, has_image=False, has_panels=True, body_block_count=1)
        assert suggestion == LayoutId.INFOGRAPHIC

    def test_image_with_multiple_blocks_suggests_infographic(self):
        suggestion = suggest_layout(has_diagram=False, has_image=True, has_panels=False, body_block_count=2)
        assert suggestion == LayoutId.INFOGRAPHIC

    def test_image_with_single_block_does_not_suggest_infographic(self):
        """Imagem sozinha, sem densidade de conteúdo, não é motivo suficiente pro padrão denso."""
        suggestion = suggest_layout(has_diagram=False, has_image=True, has_panels=False, body_block_count=1)
        assert suggestion == LayoutId.TITLE_BULLETS

    def test_plain_content_falls_back_to_title_bullets(self):
        suggestion = suggest_layout(has_diagram=False, has_image=False, has_panels=False, body_block_count=1)
        assert suggestion == LayoutId.TITLE_BULLETS


class TestSlideSignals:
    def test_extracts_diagram_flag(self):
        slide = Slide(layout=LayoutId.DIAGRAM_FULL, title="X", asset=SlideAsset(kind="diagram", ref="flowchart LR\nA-->B"))
        signals = slide_signals(slide)
        assert signals["has_diagram"] is True
        assert signals["has_image"] is False

    def test_extracts_image_flag(self):
        slide = Slide(layout=LayoutId.INFOGRAPHIC, title="X", asset=SlideAsset(kind="image", ref="job/assets/x.png"))
        signals = slide_signals(slide)
        assert signals["has_image"] is True
        assert signals["has_diagram"] is False

    def test_no_asset_means_both_flags_false(self):
        slide = Slide(layout=LayoutId.TITLE_BULLETS, title="X")
        signals = slide_signals(slide)
        assert signals["has_image"] is False
        assert signals["has_diagram"] is False

    def test_extracts_panels_flag(self):
        slide = Slide(
            layout=LayoutId.INFOGRAPHIC, title="X",
            body=[PanelListBlock(panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")])],
        )
        signals = slide_signals(slide)
        assert signals["has_panels"] is True

    def test_counts_body_blocks(self):
        slide = Slide(
            layout=LayoutId.TITLE_BULLETS, title="X",
            body=[ParagraphBlock(text="a"), BulletListBlock(items=["1", "2"])],
        )
        signals = slide_signals(slide)
        assert signals["body_block_count"] == 2


class TestClassify:
    def test_matches_suggest_layout_for_equivalent_signals(self):
        slide = Slide(
            layout=LayoutId.TITLE_BULLETS, title="X",
            body=[PanelListBlock(panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")])],
        )
        assert classify(slide) == LayoutId.INFOGRAPHIC


class TestClassifiableLayouts:
    def test_only_content_driven_layouts_are_classifiable(self):
        # cover/section-break/closing são posicionais; two-column depende de
        # "comparação", não inferível só por contagem — fora do escopo de propósito.
        assert CLASSIFIABLE_LAYOUTS == {LayoutId.TITLE_BULLETS, LayoutId.DIAGRAM_FULL, LayoutId.INFOGRAPHIC}
