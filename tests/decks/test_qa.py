"""
Testes de app/decks/qa.py.

Os helpers puros (contraste, asset não resolvido) são mockáveis/determinísticos
— testados sem Playwright. O overflow (`_check_overflow`/`check_deck`) só se
prova rodando o Chromium de verdade (mesmo raciocínio de test_pdf.py e
test_diagrams.py) — real, não mockado.
"""

import pytest

from app.decks import qa
from app.decks.models import (
    BulletListBlock,
    DeckSpec,
    LayoutId,
    Panel,
    PanelListBlock,
    ParagraphBlock,
    Slide,
    SlideAsset,
    Theme,
)


def _theme(**overrides) -> Theme:
    # primary/background com contraste suficiente entre si por padrão (2026-08-21
    # (7): Theme agora valida isso sempre, achado real em produção) — testes que
    # querem testar baixo contraste de propósito sobrescrevem explicitamente.
    defaults = {"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"}
    defaults.update(overrides)
    return Theme(palette=defaults, font_stack="Inter, sans-serif")


class TestContrastRatio:
    # Testes da função pura `_contrast_ratio` — não passam por `Theme`
    # (que hoje exige fundo escuro, ver TestThemeBackgroundMustBeDark em
    # test_models.py), então branco puro aqui é só um par de cores qualquer
    # usado pra provar a fórmula, sem nenhuma relação com aquela regra.
    def test_black_on_white_is_maximum_contrast(self):
        ratio = qa._contrast_ratio("#000000", "#FFFFFF")
        assert ratio == pytest.approx(21.0, rel=0.01)

    def test_identical_colors_have_minimum_contrast(self):
        ratio = qa._contrast_ratio("#777777", "#777777")
        assert ratio == pytest.approx(1.0, rel=0.01)

    def test_is_symmetric(self):
        assert qa._contrast_ratio("#123456", "#FEDCBA") == pytest.approx(
            qa._contrast_ratio("#FEDCBA", "#123456"), rel=0.001
        )


class TestCheckContrast:
    def test_no_issues_for_high_contrast_theme(self):
        # fundo escuro + texto quase branco = contraste máximo, mesma
        # intenção do teste de sempre, só invertendo qual ponta é clara.
        theme = _theme(primary="#FFFFFF", background="#0A0A0D", text="#F5F5F5")
        issues = qa._check_contrast(theme)
        assert issues == []

    def test_flags_low_contrast_text_on_background(self):
        theme = _theme(text="#15243A", background="#0D1B2E")  # texto quase da cor do fundo
        issues = qa._check_contrast(theme)
        assert any(i["kind"] == "low_contrast" and "text" in i["message"] for i in issues)

    def test_flags_low_contrast_background_on_primary(self):
        # cor de fundo (usada como texto nos slides de tela cheia) muito parecida com primary —
        # desde 2026-08-21 (7) o `Theme` normal nem CONSTRÓI mais com esse par (validação
        # própria, achado real em produção) — usa `model_construct` pra pular a validação e
        # ainda testar a lógica própria de `qa._check_contrast` isoladamente (defesa em
        # profundidade: o soft-check do QA continua existindo, mesmo que hoje inatingível
        # via `Theme` construído normalmente).
        theme = Theme.model_construct(
            palette={"primary": "#3355FF", "background": "#3350F5", "text": "#000000"},
            font_stack="Inter, sans-serif",
        )
        issues = qa._check_contrast(theme)
        assert any(i["kind"] == "low_contrast" and "primary" in i["message"] for i in issues)


class TestCheckUnresolvedAssets:
    def test_no_issue_when_no_asset(self):
        slide = Slide(layout=LayoutId.COVER, title="Capa")
        deck = DeckSpec(theme=_theme(), slides=[slide])
        assert qa._check_unresolved_assets(deck) == []

    def test_no_issue_when_asset_resolved_to_storage_path(self):
        slide = Slide(
            layout=LayoutId.DIAGRAM_FULL, title="X",
            asset=SlideAsset(kind="image", ref="job123/assets/slide-1.png"),
        )
        deck = DeckSpec(theme=_theme(), slides=[slide])
        assert qa._check_unresolved_assets(deck) == []

    def test_no_issue_when_asset_already_a_signed_url(self):
        slide = Slide(
            layout=LayoutId.DIAGRAM_FULL, title="X",
            asset=SlideAsset(kind="image", ref="https://x.supabase.co/sign/foo.png"),
        )
        deck = DeckSpec(theme=_theme(), slides=[slide])
        assert qa._check_unresolved_assets(deck) == []

    def test_flags_asset_still_in_prompt_form(self):
        slide = Slide(
            layout=LayoutId.DIAGRAM_FULL, title="X",
            asset=SlideAsset(kind="image", ref="um gráfico de crescimento"),
        )
        deck = DeckSpec(theme=_theme(), slides=[slide])
        issues = qa._check_unresolved_assets(deck)
        assert len(issues) == 1
        assert issues[0]["kind"] == "unresolved_asset"
        assert issues[0]["slide_id"] == slide.id


class TestCheckDeck:
    @pytest.mark.asyncio
    async def test_no_overflow_issue_for_short_content(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS,
                    title="Resumo",
                    body=[BulletListBlock(items=["Ponto curto 1", "Ponto curto 2"])],
                )
            ],
        )
        report = await qa.check_deck(deck)
        assert not any(i["kind"] == "overflow" for i in report["issues"])

    @pytest.mark.asyncio
    async def test_flags_overflow_for_excessive_content(self):
        paragrafo_longo = "Texto de teste bem longo pra forçar overflow visual. " * 40
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS,
                    title="Slide com conteúdo demais",
                    body=[ParagraphBlock(text=paragrafo_longo) for _ in range(8)],
                )
            ],
        )
        report = await qa.check_deck(deck)
        assert any(i["kind"] == "overflow" for i in report["issues"])
        assert report["issue_count"] == len(report["issues"])

    @pytest.mark.asyncio
    async def test_aggregates_all_three_kinds_of_issue(self):
        deck = DeckSpec(
            theme=_theme(text="#132132", background="#0D1B2E"),  # baixo contraste de propósito (texto quase da cor do fundo)
            slides=[
                Slide(
                    layout=LayoutId.DIAGRAM_FULL,
                    title="X",
                    asset=SlideAsset(kind="image", ref="prompt não resolvido"),
                )
            ],
        )
        report = await qa.check_deck(deck)
        kinds = {i["kind"] for i in report["issues"]}
        assert "low_contrast" in kinds
        assert "unresolved_asset" in kinds


class TestCheckLayoutSuggestion:
    """5º check (2026-08-24, Fatia C) — layout_classifier.py, ver docstring de qa.py."""

    def test_no_issue_when_diagram_full_has_diagram_asset(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[Slide(layout=LayoutId.DIAGRAM_FULL, title="X", asset=SlideAsset(kind="diagram", ref="flowchart LR\nA-->B"))],
        )
        assert qa._check_layout_suggestion(deck) == []

    def test_flags_title_bullets_when_content_has_panels(self):
        """Painéis num slide title-bullets sugerem infographic — divergência real."""
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS, title="X",
                    body=[PanelListBlock(panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")])],
                )
            ],
        )
        issues = qa._check_layout_suggestion(deck)
        assert len(issues) == 1
        assert issues[0]["kind"] == "layout_suggestion"
        assert "infographic" in issues[0]["message"]

    def test_no_issue_when_chosen_layout_matches_suggestion(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.INFOGRAPHIC, title="X",
                    body=[PanelListBlock(panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")])],
                )
            ],
        )
        assert qa._check_layout_suggestion(deck) == []

    def test_ignores_positional_layouts_even_with_sparse_content(self):
        """cover/section-break/closing/two-column ficam fora do escopo do classificador — nunca geram aviso."""
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(layout=LayoutId.COVER, title="X"),
                Slide(layout=LayoutId.SECTION_BREAK, title="Y"),
                Slide(layout=LayoutId.CLOSING, title="Z"),
                Slide(layout=LayoutId.TWO_COLUMN, title="W", body=[ParagraphBlock(text="a")]),
            ],
        )
        assert qa._check_layout_suggestion(deck) == []

    @pytest.mark.asyncio
    async def test_included_in_check_deck_report(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.TITLE_BULLETS, title="X",
                    body=[PanelListBlock(panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")])],
                )
            ],
        )
        report = await qa.check_deck(deck)
        assert any(i["kind"] == "layout_suggestion" for i in report["issues"])


class TestCheckSparseLayout:
    """
    Check novo (2026-08-21, tarefa 3) — nasceu de um achado real comparando
    nosso resultado com uma referência: conteúdo curto deixava um vão vazio
    grande entre os painéis e a citação (a citação ancora no rodapé via CSS,
    então sobra espaço no meio quando o resto é curto). Chromium real via
    Playwright, mesmo raciocínio de `_check_overflow`.
    """

    def _infographic_deck(self, panels: list[Panel], citation: str | None = "Fonte X") -> DeckSpec:
        return DeckSpec(
            theme=_theme(),
            slides=[
                Slide(
                    layout=LayoutId.INFOGRAPHIC,
                    title="Título de teste",
                    eyebrow="Categoria",
                    citation=citation,
                    body=[
                        ParagraphBlock(text="Subtítulo curto."),
                        PanelListBlock(panels=panels),
                    ],
                )
            ],
        )

    @pytest.mark.asyncio
    async def test_flags_large_gap_with_sparse_content(self):
        # 2 painéis bem curtos + citação — deixa vão grande entre eles (o
        # cenário real que motivou o check, achado comparando com a referência)
        deck = self._infographic_deck(
            panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")]
        )
        report = await qa.check_deck(deck)
        assert any(i["kind"] == "sparse_layout" for i in report["issues"])

    @pytest.mark.asyncio
    async def test_no_issue_when_no_citation_and_content_fills_naturally(self):
        # sem citação, painéis logo após o subtítulo — sem elemento ancorado
        # no rodapé pra criar um vão grande depois deles
        deck = self._infographic_deck(
            panels=[Panel(heading="A", text="x"), Panel(heading="B", text="y")],
            citation=None,
        )
        report = await qa.check_deck(deck)
        assert not any(i["kind"] == "sparse_layout" for i in report["issues"])

    @pytest.mark.asyncio
    async def test_no_issue_for_non_infographic_layout(self):
        deck = DeckSpec(
            theme=_theme(),
            slides=[Slide(layout=LayoutId.TITLE_BULLETS, title="X", body=[BulletListBlock(items=["um"])])],
        )
        report = await qa.check_deck(deck)
        assert not any(i["kind"] == "sparse_layout" for i in report["issues"])
