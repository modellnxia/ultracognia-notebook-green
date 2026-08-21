"""Testes de app/decks/steps.py — orquestração isolada (PDF/Storage/LLM/diagramas já provados à parte)."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.decks import steps
from app.decks.llm.images import ImageGenerationError
from app.decks.diagrams import DiagramRenderError
from app.decks.models import DeckSpec, Slide, SlideAsset, LayoutId, Theme


def _valid_deck() -> DeckSpec:
    return DeckSpec(
        theme=Theme(
            palette={"primary": "#1A2B3C", "background": "#0D1B2E", "text": "#F2F2F2"},
            font_stack="Inter, system-ui, sans-serif",
        ),
        slides=[Slide(layout=LayoutId.COVER, title="Capa")],
    )


class TestRunStructure:
    @pytest.mark.asyncio
    async def test_reads_editorial_and_returns_deckspec_json(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={"editorial_text": "editorial colado pelo usuário", "llm_provider": None}
        )
        deck = _valid_deck()

        with patch(
            "app.decks.steps.generate_deck_structure",
            new=AsyncMock(return_value=(deck, 100, 200)),
        ) as m_generate:
            output_ref, input_tok, output_tok, cost = await steps.run_structure(job_id, conn)

        m_generate.assert_awaited_once_with(
            "editorial colado pelo usuário", conn, preferred_provider=None
        )
        assert json.loads(output_ref) == json.loads(deck.model_dump_json())
        assert (input_tok, output_tok, cost) == (100, 200, 0)

    @pytest.mark.asyncio
    async def test_passes_llm_provider_choice_through(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={"editorial_text": "editorial", "llm_provider": "deepseek"}
        )
        deck = _valid_deck()

        with patch(
            "app.decks.steps.generate_deck_structure",
            new=AsyncMock(return_value=(deck, 10, 20)),
        ) as m_generate:
            await steps.run_structure(job_id, conn)

        m_generate.assert_awaited_once_with("editorial", conn, preferred_provider="deepseek")

    @pytest.mark.asyncio
    async def test_raises_when_job_not_found(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match=str(job_id)):
            await steps.run_structure(job_id, conn)


class TestRunAssets:
    @pytest.mark.asyncio
    async def test_passthrough_when_no_slide_has_asset(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()  # slide único, sem asset
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()) as m_bucket,
            patch("app.decks.steps.generate_image", new=AsyncMock()) as m_image,
            patch("app.decks.steps.render_diagram_png", new=AsyncMock()) as m_diagram,
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, input_tok, output_tok, cost = await steps.run_assets(job_id, conn)

        m_bucket.assert_awaited_once()
        m_image.assert_not_awaited()
        m_diagram.assert_not_awaited()
        m_upload.assert_not_awaited()
        assert json.loads(output_ref) == json.loads(deck.model_dump_json())
        assert (input_tok, output_tok, cost) == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_resolves_image_asset_and_uploads(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        deck.slides[0].asset = SlideAsset(kind="image", ref="um gráfico de crescimento")
        slide_id = deck.slides[0].id
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()),
            patch(
                "app.decks.steps.generate_image",
                new=AsyncMock(return_value=(b"fake-png-bytes", "image/png")),
            ) as m_image,
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, *_ = await steps.run_assets(job_id, conn)

        m_image.assert_awaited_once_with("um gráfico de crescimento")
        m_upload.assert_awaited_once_with(
            f"{job_id}/assets/{slide_id}.png", b"fake-png-bytes", "image/png"
        )
        updated_deck = DeckSpec.model_validate(json.loads(output_ref))
        assert updated_deck.slides[0].asset.ref == f"{job_id}/assets/{slide_id}.png"

    @pytest.mark.asyncio
    async def test_resolves_diagram_asset_and_uploads(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        deck.slides[0].asset = SlideAsset(kind="diagram", ref="flowchart LR\nA-->B")
        slide_id = deck.slides[0].id
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()),
            patch(
                "app.decks.steps.render_diagram_png", new=AsyncMock(return_value=b"fake-png-bytes")
            ) as m_diagram,
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, *_ = await steps.run_assets(job_id, conn)

        m_diagram.assert_awaited_once_with("flowchart LR\nA-->B")
        m_upload.assert_awaited_once_with(
            f"{job_id}/assets/{slide_id}.png", b"fake-png-bytes", "image/png"
        )
        updated_deck = DeckSpec.model_validate(json.loads(output_ref))
        assert updated_deck.slides[0].asset.ref == f"{job_id}/assets/{slide_id}.png"

    @pytest.mark.asyncio
    async def test_keeps_placeholder_when_image_generation_fails(self):
        """Resiliente por design — um asset quebrado não derruba o job inteiro."""
        job_id = uuid.uuid4()
        deck = _valid_deck()
        deck.slides[0].asset = SlideAsset(kind="image", ref="prompt original")
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()),
            patch(
                "app.decks.steps.generate_image",
                new=AsyncMock(side_effect=ImageGenerationError("API fora do ar")),
            ),
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, *_ = await steps.run_assets(job_id, conn)

        m_upload.assert_not_awaited()
        updated_deck = DeckSpec.model_validate(json.loads(output_ref))
        assert updated_deck.slides[0].asset.ref == "prompt original"  # intocado

    @pytest.mark.asyncio
    async def test_keeps_placeholder_when_diagram_render_fails(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        deck.slides[0].asset = SlideAsset(kind="diagram", ref="flowchart LR\nA-->B")
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()),
            patch(
                "app.decks.steps.render_diagram_png",
                new=AsyncMock(side_effect=DiagramRenderError("Chromium não subiu")),
            ),
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, *_ = await steps.run_assets(job_id, conn)

        m_upload.assert_not_awaited()
        updated_deck = DeckSpec.model_validate(json.loads(output_ref))
        assert updated_deck.slides[0].asset.ref == "flowchart LR\nA-->B"  # intocado

    @pytest.mark.asyncio
    async def test_raises_when_structure_step_not_done_yet(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="structure"):
            await steps.run_assets(job_id, conn)


class TestRunRender:
    @pytest.mark.asyncio
    async def test_orchestrates_html_pdf_upload_in_order(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.render_deck_html", return_value="<html>x</html>") as m_html,
            patch("app.decks.steps.render_deck_pdf", new=AsyncMock(return_value=b"%PDF-fake")) as m_pdf,
            patch("app.decks.steps.render_deck_pptx", new=AsyncMock(return_value=b"PK-fake-pptx")) as m_pptx,
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()) as m_bucket,
            patch("app.decks.steps.upload_object", new=AsyncMock()) as m_upload,
        ):
            output_ref, input_tok, output_tok, cost = await steps.run_render(job_id, conn)

        m_html.assert_called_once()
        m_pdf.assert_awaited_once_with("<html>x</html>")
        m_pptx.assert_awaited_once()
        m_bucket.assert_awaited_once()
        assert m_upload.await_count == 2
        m_upload.assert_any_await(f"{job_id}/deck.pdf", b"%PDF-fake", "application/pdf")
        m_upload.assert_any_await(
            f"{job_id}/deck.pptx", b"PK-fake-pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        assert json.loads(output_ref) == {"pdf": f"{job_id}/deck.pdf", "pptx": f"{job_id}/deck.pptx"}
        assert (input_tok, output_tok, cost) == (0, 0, 0)  # custo já contado nas etapas anteriores

    @pytest.mark.asyncio
    async def test_signs_url_for_resolved_asset_before_rendering(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        object_path = f"{job_id}/assets/{deck.slides[0].id}.png"
        deck.slides[0].asset = SlideAsset(kind="image", ref=object_path)
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})

        with (
            patch("app.decks.steps.render_deck_html", return_value="<html>x</html>") as m_html,
            patch("app.decks.steps.render_deck_pdf", new=AsyncMock(return_value=b"%PDF-fake")),
            patch("app.decks.steps.render_deck_pptx", new=AsyncMock(return_value=b"PK-fake-pptx")),
            patch("app.decks.steps.ensure_bucket_exists", new=AsyncMock()),
            patch("app.decks.steps.upload_object", new=AsyncMock()),
            patch(
                "app.decks.steps.create_signed_url",
                new=AsyncMock(return_value=f"https://x.supabase.co/sign/{object_path}?token=abc"),
            ) as m_sign,
        ):
            await steps.run_render(job_id, conn)

        m_sign.assert_awaited_once_with(object_path)
        rendered_deck = m_html.call_args.args[0]
        assert rendered_deck.slides[0].asset.ref == f"https://x.supabase.co/sign/{object_path}?token=abc"
        # o deck original (lido do banco) não foi mutado
        assert deck.slides[0].asset.ref == object_path

    @pytest.mark.asyncio
    async def test_raises_when_assets_step_not_done_yet(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="assets"):
            await steps.run_render(job_id, conn)


class TestRunQa:
    @pytest.mark.asyncio
    async def test_runs_checks_against_assets_step_deck_and_returns_report(self):
        job_id = uuid.uuid4()
        deck = _valid_deck()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"output_ref": deck.model_dump_json()})
        fake_report = {"issues": [{"slide_id": "x", "kind": "overflow", "message": "..."}], "issue_count": 1}

        with patch(
            "app.decks.steps.check_deck", new=AsyncMock(return_value=fake_report)
        ) as m_check:
            output_ref, input_tok, output_tok, cost = await steps.run_qa(job_id, conn)

        called_deck = m_check.call_args.args[0]
        assert called_deck.slides[0].title == deck.slides[0].title
        assert json.loads(output_ref) == fake_report
        assert (input_tok, output_tok, cost) == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_raises_when_assets_step_not_done_yet(self):
        job_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="assets"):
            await steps.run_qa(job_id, conn)
